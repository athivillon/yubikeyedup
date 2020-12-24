import sqlite3


class SQL:
    REQUESTS = {
        'yubico_get_key':	 'SELECT aeskey, aead, internalname, counter, time FROM yubikeys WHERE publicname = ? AND active = 1',
        'yubico_update_counter': 'UPDATE yubikeys SET counter = ?, time = ? WHERE publicname = ?',

        'oath_get_token':	 'SELECT counter, secret FROM oathtokens WHERE publicname = ? AND active = 1',
        'oath_update_counter':	 'UPDATE oathtokens SET counter = ? WHERE publicname = ?',

        'get_api_secret':	 'SELECT secret from apikeys WHERE id = ?',
        'get_param':             'SELECT value from params WHERE param = ?',
    }

    def __init__(self, con):
        self.con = con
        self.cur = self.con.cursor()

    def select(self, req, param):
        self.cur.execute(self.REQUESTS[req], param)
        self.result = self.cur.fetchone()
        return self.result != None

    def update(self, req, param):
        self.con.execute(self.REQUESTS[req], param)
        self.con.commit()

    def get_parameter(self, param, default):
        try :
           self.result = None
           self.cur.execute(self.REQUESTS['get_param'], [param] )
           self.result = self.cur.fetchone()[0]
        except:
           pass
        if self.result == None:
           return default
        else:
           return self.result


def connect_to_db(filename):
    return sqlite3.connect(filename, check_same_thread=False)

