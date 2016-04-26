from tempest import config

try:
    import winrm
except ImportError:
    raise Exception("PyWinrm is not installed")

CONF = config.CONF


class WinrmClient(object):
    _URL_TEMPLATE = '%(protocol)s://%(ip)s:%(port)s/wsman'

    def __init__(self, server_ip, username, password, timeout=None):
        _url = self._get_url(server_ip)
        self._conn = winrm.protocol.Protocol(
            endpoint=_url, username=username, password=password)
        self._conn.set_timeout(int(timeout))

    def exec_cmd(self, command, args=(), check_output=True):
        shell_id = self._conn.open_shell()
        command_id = self._conn.run_command(shell_id, command, args)
        if check_output is True:
            rs = winrm.Response(
                self._conn.get_command_output(shell_id, command_id))
            if rs.status_code != 0:
                raise Exception("%s %s %s" % (rs.status_code, rs.std_err, rs.std_out))
            else:
                return rs
        return None

    def run_powershell(self, script):
        cmd = 'powershell %s' % script
        return self.exec_cmd(cmd)

    def _get_url(self, ip):
        return self._URL_TEMPLATE % {'protocol': 'https',
                                     'ip': ip,
                                     'port': 5986}
