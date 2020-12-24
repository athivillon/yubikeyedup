import re

from Cryptodome.Cipher import AES

from yubihsm import YubiHsm
from yubihsm.defs import CAPABILITY, ALGORITHM, OBJECT
from yubihsm.objects import AsymmetricKey
import yubihsm.exceptions

from sql import *
import yubistatus


class Validate:
    def __init__(self, sql):
        self.sql = sql
        self.yubihsm_url  = sql.get_parameter('yubihsm_url',None)
        self.aeadkey_id   = int(sql.get_parameter('aeadkey_id','8920'),16)
        self.yubihsm_auth = int(sql.get_parameter('yubihsm_auth', '1'),16)
        self.yubihsm_pass = sql.get_parameter('yubihsm_pass', 'password')
       

class Yubico(Validate):
    # sorry for this one-liner
    modhex = ''.join(dict([ ('cbdefghijklnrtuv'[i], '0123456789abcdef'[i]) for i in range(16)] ).get(chr(j), '?') for j in range(256))

    def set_params(self, params, answer):
        if 'nonce' not in params:
            return yubistatus.MISSING_PARAMETER

        answer['otp'] = params['otp']
        answer['nonce'] = params['nonce']
        answer['sl'] = '100'

        self.otp = params['otp']

        return yubistatus.OK

    def modhexdecode(self, string):
        return bytes.fromhex(string.translate(self.modhex))

    def CRC(self, data):
        crc = 0xffff
        for b in data:
            crc ^= (b & 0xff)
            for j in range(0, 8):
                n = crc & 1
                crc >>= 1
                if n != 0:
                    crc ^= 0x8408
        return crc
   
    def validate(self):
        match = re.match('([cbdefghijklnrtuv]{0,16})([cbdefghijklnrtuv]{32})', self.otp)
        if not match:
            # this should not happen because otp matches YubiHTTPServer.PARAM_REGEXP
            return yubistatus.BACKEND_ERROR

        userid, token = match.groups()

        if not self.sql.select('yubico_get_key', [userid]):
            return yubistatus.BAD_OTP
        aeskey, aead, internalname, counter, time = self.sql.result
        if aeskey == None and aead == None :
              return yubistatus.BAD_OTP

        if (aeskey != None) :

            aes = AES.new(bytes.fromhex(aeskey), AES.MODE_ECB)
            plaintext = aes.decrypt(self.modhexdecode(token)).hex()
    
            if internalname != plaintext[:12]:
                return yubistatus.BAD_OTP
    
            # if self.CRC(plaintext[:32].decode('hex')) != 0xf0b8:
            if self.CRC(bytes.fromhex(plaintext[:32])) != 0xf0b8:
                return yubistatus.BAD_OTP
    
            internalcounter = int(plaintext[14:16] + plaintext[12:14] + plaintext[22:24], 16)
            if counter >= internalcounter:
                return yubistatus.REPLAYED_OTP
    
            timestamp = int(plaintext[20:22] + plaintext[18:20] + plaintext[16:18], 16)
            if time >= timestamp and (counter >> 8) == (internalcounter >> 8):
                return yubistatus.BAD_OTP

        elif self.yubihsm_url != None :
            # try AEAD
            try : 
               hsm = YubiHsm.connect(self.yubihsm_url)
               session = hsm.create_session_derived(self.yubihsm_auth, self.yubihsm_pass)

               aead_key = session.get_object(self.aeadkey_id, OBJECT.OTP_AEAD_KEY  )

            except yubihsm.exceptions.YubiHsmError as e :
               print("Error connecting to YubiHSM",e)
               return yubistatus.BACKEND_ERROR

            try : 
               aead1 = bytes.fromhex(aead)
               otp_return = aead_key.decrypt_otp(aead1, self.modhexdecode(token))
               internalcounter =( otp_return.use_counter << 8) + otp_return.session_counter
               if counter >= internalcounter :
                  print("counter error",counter,internalcounter)
                  return yubistatus.REPLAYED_OTP
               timestamp = (otp_return.timestamp_high << 16) + otp_return.timestamp_low
               if time >= timestamp and (counter >> 8) == otp_return.use_counter :
                  print("timestamp error",time,timestamp)
                  return yubistatus.BAD_OTP

            except yubihsm.exceptions.YubiHsmDeviceError as yubierror:
               print("Error decoding OTP",yubierror)
               return yubistatus.BAD_OTP

            except yubihsm.exceptions.YubiHsmError as yubierror:
               print("Error decoding OTP",yubierror)
               return yubistatus.BAD_OTP

            finally:
               # Clean up
               session.close()
               hsm.close()

        else:
            return yubistatus.BACKEND_ERROR

    
        self.sql.update('yubico_update_counter', [internalcounter, timestamp, userid])

        return yubistatus.OK


class OATH(Validate):
    def set_params(self, params, answer):
        if len(otp) in [ 18, 20 ]:
            publicid = otp[0:12]
            oath = params['otp'][12:]
        elif len(otp) in [ 6, 8 ]:
            if 'publicid' not in params:
                return yubistatus.MISSING_PARAMETER
            publicid = params['publicid']
            oath = params['otp']
        else:
            return yubistatus.BAD_OTP

        answer['otp'] = params['otp']

        self.oath = oath
        self.publicid = publicid

        return yubistatus.OK

    def test_hotp(self, key, counter, digits=6):
        counter = str(counter).rjust(16, '0').decode('hex')
        hs = hmac.new(key, counter, hashlib.sha1).digest()
        offset = ord(hs[19]) & 0xF
        bin_code = int((chr(ord(hs[offset]) & 0x7F) + hs[offset+1:offset+4]).encode('hex'), 16)
        return str(bin_code)[-digits:]

    def validate(self):
        # XXX: TODO, it hasn't been tested
        return yubistatus.BACKEND_ERROR

        if len(self.oath) % 2 != 0:
            return yubistatus.BAD_OTP

        if not self.sql.select('oath_get_token', [publicid]):
            return yubistatus.BAD_OTP

        actualcounter, key = self.sql.result
        key = key.decode('hex')
        for counter in range(actualcounter + 1, actualcounter + 256):
            if self.oath == self.test_hotp(key, counter, len(self.oath)):
                self.sql.update('yubico_update_counter', [str(counter), self.publicid])
                return yubistatus.OK

        return yubistatus.BAD_OTP
