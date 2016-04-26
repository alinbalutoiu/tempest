import base64

from oslo_log import log as logging

from tempest import config
from tempest import test
from tempest.scenario import manager
from tempest.common.utils.windows import winrm_client

CONF = config.CONF

LOG = logging.getLogger(__name__)


class TestHypervOVS(manager.NetworkScenarioTest):

    """
    This test checks the network data transfer through
    TCP or UDP between 2 VMs when they are:

     * On different nodes
     * On the same node
    """
    _host_key = 'OS-EXT-SRV-ATTR:host'

    @classmethod
    def setup_clients(cls):
        super(TestHypervOVS, cls).setup_clients()
        cls.admin_servers_client = cls.os_adm.servers_client
        cls.hypervisor_client = cls.os_adm.hypervisor_client

    def setUp(self):
        super(TestHypervOVS, self).setUp()
        # Credentials for images
        self.win_user = CONF.hyperv.win_user
        self.win_pass = CONF.hyperv.win_pass
        self.image_ssh_user = CONF.hyperv.image_ssh_user
        self.image_alt_ssh_user = CONF.hyperv.image_alt_ssh_user
        # Images used for test
        self.image_ref = CONF.hyperv.image_hyperv_ref
        self.image_ref_alt = CONF.hyperv.image_hyperv_ref_alt
        self.flavor = CONF.hyperv.flavor_hyperv_ref
        self.timeout = CONF.hyperv.ssh_timeout
        # Size or time testing
        self.size_to_test = CONF.hyperv.size_test
        self.size_to_test = None
        self.time_to_test = CONF.hyperv.time_test
        # Check if images exist and if they have supported hypervisors and OSs
        self._check_image(self.image_ref)
        self._check_image(self.image_ref_alt)
        self.hyper_1 = self._get_image_property(self.image_ref,
                                                'hypervisor_type')
        self.hyper_2 = self._get_image_property(self.image_ref_alt,
                                                'hypervisor_type')

    def _check_image(self, image):
        try:
            self.image_client.get_image_meta(image)
        except Exception:
            raise Exception(
                'Image with id %(image)s was not found (make sure the image '
                'exists and is public)' % {'image': image})

        self._get_image_property(image, 'os_distro')

        supported_hyper_types = ['qemu', 'hyperv']
        hypervisor = self._get_image_property(image, 'hypervisor_type')
        if hypervisor not in supported_hyper_types:
            raise Exception(
                'Hypervsior type used (%(hypervisor)s) for image %(image)s '
                'is not supported, use qemu or hyperv' %
                {'hypervisor': hypervisor, 'image': image})

    def _get_image_property(self, image_id, img_prop):
        try:
            property_image = self.image_client.get_image_meta(
                image_id)['properties'][img_prop]
        except KeyError:
            raise Exception('Please set %(img_prop)s for image %(image_id)s'
                            % {'img_prop': img_prop, 'image_id': image_id})
        return property_image

    def _get_server_details(self, server_id):
        body = self.admin_servers_client.show_server(server_id)
        return body

    def _get_host_for_server(self, server_id):
        return self._get_server_details(server_id)[self._host_key]

    def _get_host_hypervisor_type(self, hostname):
        hyper_id = self.hypervisor_client.search_hypervisor(hostname)[0]['id']
        host = self.hypervisor_client.show_hypervisor(hyper_id)
        return host['hypervisor_type']

    def _get_host_other_than(self, host):
        hyper_type = self._get_host_hypervisor_type(hostname=host)
        for target_host in self.hypervisor_client.list_hypervisors():
            hostname = target_host['hypervisor_hostname']
            if host != hostname:
                if self._get_host_hypervisor_type(hostname) == hyper_type:
                    return hostname
        raise Exception('Not enough hypervisors of type: %(hyper_type)s' %
                        {'hyper_type': hyper_type})

    def add_keypair_secgroup_userdata(self):
        self.keypair = self.create_keypair()
        self.secgroup = self._create_security_group()
        # user-data to allow iperf3 TCP/UDP and winrm http
        self.user_data = base64.b64encode(
            '#ps1\nNew-NetFirewallRule -DisplayName "Allow iperf3 TCP" '
            '-LocalPort 5201 -Protocol TCP\n'
            'New-NetFirewallRule -DisplayName "Allow iperf3 UDP" '
            '-LocalPort 5201 -Protocol UDP\n'
            'New-NetFirewallRule -DisplayName "Allow winrm http" '
            '-LocalPort 5985 -Protocol TCP')

    def add_floating_ip(self, instance):
        ip = self.create_floating_ip(thing=instance)
        return ip.floating_ip_address

    def boot_instance(self, image=None, flavor=None, availability_zone=None):
        create_kwargs = {
            'key_name': self.keypair['name'],
            'security_groups': [{'name': self.secgroup['name']}],
            'availability_zone': availability_zone,
            'user_data': self.user_data,
        }
        return self.create_server(
            image=image, flavor=flavor, create_kwargs=create_kwargs)

    def boot_two_instances_with_floatingips(self, img_1, img_2, diff_host):
        os_1 = self._get_image_property(img_1, 'os_distro')
        os_2 = self._get_image_property(img_2, 'os_distro')

        self.instance_1 = self.boot_instance(image=img_1, flavor=self.flavor)
        self.floatingip_1 = self.add_floating_ip(self.instance_1)
        self.wait_active(self.floatingip_1, os=os_1)

        hyper_type_1 = self._get_image_property(img_1, 'hypervisor_type')
        hyper_type_2 = self._get_image_property(img_2, 'hypervisor_type')

        host = None
        if hyper_type_1 == hyper_type_2:
            host = self._get_host_for_server(self.instance_1['id'])
            if diff_host:
                host = self._get_host_other_than(host)
            host = 'nova:%(host)s' % {'host': host}

        self.instance_2 = self.boot_instance(
            image=img_2, flavor=self.flavor, availability_zone=host)
        self.floatingip_2 = self.add_floating_ip(self.instance_2)
        self.wait_active(self.floatingip_2, os=os_2)

    def get_cmd(self, ip, tcp):
        """Size is preferred over time for test."""
        if not self.size_to_test:
            option = '-t %(time)s' % {'time': self.time_to_test}
        else:
            option = '-n %(size)s' % {'size': self.size_to_test}

        if tcp:
            cmd = 'iperf3 -c %(ip)s -f g -N -P8 -O5 -i 60 -t 180' % {
                'ip': ip}
        else:
            cmd = 'iperf3 -c %(ip)s -u -f g -b 0 -i 60 -t 180' % {
                'ip': ip}

        return cmd

    def get_ssh(self, ip, username):
        return self.get_remote_client(server_or_ip=ip, username=username)

    def send_winrm_iperf3_server(self, ip):
        client = winrm_client.WinrmClient(
            ip, self.win_user, self.win_pass, timeout=self.timeout)
        client.run_powershell("Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled False")
        client.exec_cmd('C:\\Users\\Administrator\\Downloads\\iperf-3.1.2-win64\\iperf3 -s -d', check_output=False)

    def send_winrm_iperf3_client(self, host_ip, to_ip):
        client = winrm_client.WinrmClient(
            host_ip, self.win_user, self.win_pass, timeout=self.timeout)
        client.run_powershell("Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled False")
        try:
            cmd = 'C:\\Users\\Administrator\\Downloads\\iperf-3.1.2-win64\\%(cmd)s' % {'cmd': self.get_cmd(to_ip, tcp=True)}
            LOG.error(cmd)
            self.tcp_result = client.exec_cmd(cmd)
            cmd = 'C:\\Users\\Administrator\\Downloads\\iperf-3.1.2-win64\\%(cmd)s' % {'cmd': self.get_cmd(to_ip, tcp=False)}
            LOG.error(cmd)
            # self.udp_result = client.exec_cmd(cmd)
            self.udp_result = None

            self.tcp_result = self.tcp_result.std_out
            # self.udp_result = self.udp_result.std_out
        except Exception:
            raise

    def send_ssh_iperf3_server(self, client):
        client.exec_command('screen -d -m iperf3 -s')

    def send_ssh_iperf3_client(self, client, to_ip):
        self.tcp_result = client.exec_command(self.get_cmd(to_ip, tcp=True))
        self.udp_result = client.exec_command(self.get_cmd(to_ip, tcp=False))

    def send_iperf3_server(self, ip, os, ssh_user):
        if os == 'windows':
            self.send_winrm_iperf3_server(ip=ip)
        else:
            self.send_ssh_iperf3_server(self.get_ssh(ip=ip, username=ssh_user))

    def send_iperf3_client(self, host_ip, to_ip, os_host, ssh_user):
        if os_host == 'windows':
            self.send_winrm_iperf3_client(host_ip, to_ip)
        else:
            self.send_ssh_iperf3_client(
                self.get_ssh(ip=host_ip, username=ssh_user), to_ip)

    def wait_active(self, ip_to_wait, os=None):
        # NOTE(abalutoiu): Windows will have to restart after cloudbase-init
        # finishes setting the hostname, so the test should wait for the VM
        # to reboot. Since the VM status doesn't when is rebooting, ping is
        # used to detect when the VM finishes rebooting.
        try:
            if os != 'windows':
                self.assertTrue(self.ping_ip_address(ip_address=ip_to_wait))
            else:
                self.assertTrue(self.ping_ip_address(ip_address=ip_to_wait))
                self.assertTrue(self.ping_ip_address(ip_address=ip_to_wait,
                                                     should_succeed=False))
                self.assertTrue(self.ping_ip_address(ip_address=ip_to_wait))
        except Exception:
            raise Exception('Timed out waiting for %(ip)s to become reachable'
                            % {'ip': ip_to_wait})

    def log_results(self, img_1, img_2, os_1, os_2, diff_host, reverse):
        type_1 = self._get_image_property(img_1, 'hypervisor_type')
        type_2 = self._get_image_property(img_2, 'hypervisor_type')

        if not diff_host:
            text = 'on the same host'
        else:
            text = 'on different hosts'
        LOG.error('Results for VMs %s' % text)
        LOG.error('First VM (%s) - Second VM (%s)' % (os_1, os_2))

        result = 'Results for %s - %s ' % (type_1, type_2)
        if not reverse:
            result = result + '(receiver - sender)'
        else:
            result = result + '(sender - receiver)'
        LOG.error(result)

        LOG.error('TCP test results: %s' % self.tcp_result)
        LOG.error('UDP test results: %s' % self.udp_result)

    def prepare_client_linux(self, client):
        try:
            client.exec_command('which iperf3')
            return  # return if command has exit code 0 (iperf3 installed)
        except Exception:
            pass  # exit code 1 (iperf3 uninstalled)

        try:  # exit code 127 if apt-get not found and exception is raised
            client.exec_command('apt-get')
            apt_get = True  # no exception raised, using apt-get
        except Exception:
            apt_get = False  # exception raised, using yum

        if apt_get:
            client.exec_command(
                'sudo add-apt-repository "ppa:patrickdk/general-lucid" -y')
            client.exec_command('sudo apt-get update')
            client.exec_command('sudo apt-get install iperf3')
        else:
            client.exec_command('sudo firewall-cmd --permanent '
                                '--zone=public --add-port=5201/tcp')
            client.exec_command('sudo firewall-cmd --permanent '
                                '--zone=public --add-port=5201/udp')
            client.exec_command('sudo firewall-cmd --reload')

            client.exec_command('sudo yum install screen -y')
            client.exec_command('sudo yum install epel-release -y')
            """
            yum check-update returns an exit code of 100 if there are
            updates available. It will raise an exception which
            should be skipped
            """
            try:
                client.exec_command('sudo yum check-update')
            except Exception:
                pass
            client.exec_command('sudo yum install iperf3 -y')

    def prepare_client_windows(self, ip):
        client = winrm_client.WinrmClient(
            ip, self.win_user, self.win_pass, timeout=self.timeout)

        try:
            client.run_powershell('Get-Command iperf3')
            return  # if iperf3 is installed, the command will succeed
        except:
            pass  # ieprf3 not found, install it

        _retry_count = 0
        _max_retry_number = 3
        try:
            url = 'http://files.budman.pw/iperf3_10.zip'
            loc = 'C:\iperf3.zip'
            cmd = 'powershell wget %(url)s -OutFile %(loc)s' % {
                'url': url, 'loc': loc}

            while True:
                try:
                    client.exec_cmd(cmd)
                    break
                except Exception:
                    _retry_count += 1
                    if _retry_count == _max_retry_number:
                        raise Exception

            target_dir = 'C:\\'
            cmd = ("$shellApplication = new-object -com shell.application\n"
                   "$zipPackage = $shellApplication.NameSpace('%(loc)s')\n"
                   "$destinationFolder = $shellApplication."
                   "NameSpace('%(target_dir)s')\n"
                   "$destinationFolder.CopyHere($zipPackage.Items())\n"
                   % {'loc': loc, 'target_dir': target_dir})
            client.run_powershell(cmd)
        except Exception:
            raise
            raise Exception('Could not install iperf3 for windows')

    def prepare_client(self, ip, os, ssh_user):
        if os == 'windows':
            self.prepare_client_windows(ip=ip)
        else:
            self.prepare_client_linux(self.get_ssh(ip=ip, username=ssh_user))

    def _test_with_images(self, img_1, img_2,
                          ssh_user_1, ssh_user_2, diff_host):
        self.boot_two_instances_with_floatingips(img_1, img_2, diff_host)

        os_1 = self._get_image_property(img_1, 'os_distro')
        os_2 = self._get_image_property(img_2, 'os_distro')
        # self.prepare_client(self.floatingip_1, os_1, ssh_user_1)
        # self.prepare_client(self.floatingip_2, os_2, ssh_user_2)

        # First VM (img_1) is sender - second VM (img_2) is receiver
        self.send_iperf3_server(self.floatingip_2, os_2, ssh_user_2)
        _, ip4 = self._get_server_port_id_and_ip4(self.instance_2)
        self.send_iperf3_client(self.floatingip_1, ip4, os_1, ssh_user_1)
        self.log_results(img_1, img_2, os_1, os_2, diff_host, reverse=True)
        # First VM (img_1) is receiver - second VM (img_2) is sender
        self.send_iperf3_server(self.floatingip_1, os_1, ssh_user_1)
        _, ip4 = self._get_server_port_id_and_ip4(self.instance_1)
        self.send_iperf3_client(self.floatingip_2, ip4, os_2, ssh_user_2)
        self.log_results(img_1, img_2, os_1, os_2, diff_host, reverse=False)

    @test.services('compute')
    def test_diff_host(self):
        self.add_keypair_secgroup_userdata()
        self._test_with_images(
            self.image_ref, self.image_ref_alt,
            self.image_ssh_user, self.image_alt_ssh_user,
            diff_host=True
        )

    @test.services('compute')
    def test_same_host(self):
        if self.hyper_1 == self.hyper_2:
            self.add_keypair_secgroup_userdata()
            self._test_with_images(
                self.image_ref, self.image_ref_alt,
                self.image_ssh_user, self.image_alt_ssh_user,
                diff_host=False
            )

    @test.services('compute')
    def test_same_host_first_image(self):
        if self.hyper_1 != self.hyper_2:
            self.add_keypair_secgroup_userdata()
            self._test_with_images(
                self.image_ref, self.image_ref,
                self.image_ssh_user, self.image_ssh_user,
                diff_host=False
            )

    @test.services('compute')
    def test_same_host_second_image(self):
        if self.hyper_1 != self.hyper_2:
            self.add_keypair_secgroup_userdata()
            self._test_with_images(
                self.image_ref_alt, self.image_ref_alt,
                self.image_alt_ssh_user, self.image_alt_ssh_user,
                diff_host=False
            )
