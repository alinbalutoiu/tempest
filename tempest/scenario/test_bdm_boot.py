# Copyright (c) 2015 Cloudbase Solutions SRL
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from oslo_log import log

from tempest import config
from tempest.scenario import manager
from tempest import test
from tempest_lib import exceptions

CONF = config.CONF

LOG = log.getLogger(__name__)


class TestBDMBoot(manager.ScenarioTest):
    _MIN_EPH_FLV_REQ = 3
    _FLV_EPH = 'OS-FLV-EXT-DATA:ephemeral'

    @classmethod
    def resource_setup(cls):
        super(TestBDMBoot, cls).resource_setup()
        cls.flavor_with_ephemeral = None
        cls.flavor_without_ephemeral = None

        eph_flavor_ref = cls.flavors_client.show_flavor(
            CONF.compute.flavor_ref)['flavor'][cls._FLV_EPH]
        eph_flavor_ref_alt = cls.flavors_client.show_flavor(
            CONF.compute.flavor_ref_alt)['flavor'][cls._FLV_EPH]

        if eph_flavor_ref >= cls._MIN_EPH_FLV_REQ:
            cls.flavor_with_ephemeral = CONF.compute.flavor_ref

        if eph_flavor_ref_alt == 0:
            cls.flavor_without_ephemeral = CONF.compute.flavor_ref_alt

    def setUp(self):
        super(TestBDMBoot, self).setUp()
        self.image_ref = CONF.compute.image_ref
        self.keypair = self.create_keypair()
        self.security_group = self._create_security_group()

    def flavor_clean_up(self, flavor_id):
        self.flavors_client.delete_flavor(flavor_id)
        self.flavors_client.wait_for_resource_deletion(flavor_id)

    def _create_volume_empty_or_from_image(self, imageRef=None, size=None):
        vol_name = 'volume_name'
        if imageRef:
            vol = self.create_volume(name=vol_name,
                                     size=size,
                                     imageRef=self.image_ref)
        else:
            vol = self.create_volume(name=vol_name,
                                     size=size)
        return vol

    def add_floating_ip(self, instance):
        return self.create_floating_ip(thing=instance)['ip']

    def get_ssh(self, ip):
        return self.get_remote_client(server_or_ip=ip,
                                      username=CONF.compute.image_ssh_user)

    def run_cmd(self, ssh_client, cmd):
        return ssh_client.exec_command(cmd)

    def add_to_bd_map_v2(self, curr_bd_map_v2, src_type, dest_type, **kwargs):
        bd_map_v2_to_be_added = [{
            'source_type': src_type,
            'destination_type': dest_type,
        }]
        optional_args = ['boot_index', 'disk_bus', 'uuid', 'volume_size',
                         'format_type', 'delete_on_termination']
        for arg in optional_args:
            if arg in kwargs:
                bd_map_v2_to_be_added[0][arg] = kwargs[arg]

        curr_bd_map_v2 += bd_map_v2_to_be_added

        return curr_bd_map_v2

    def _boot_instance_with_bd_map_v2(self, bd_map_v2, flavor_ref,
                                      image_ref=''):
        if image_ref != '':
            bd_map_v2 += [{
                'uuid': image_ref,
                'source_type': 'image',
                'destination_type': 'local',
                'delete_on_termination': True,
                'boot_index': 0,
            }]

        create_kwargs = {
            'block_device_mapping_v2': bd_map_v2,
            'key_name': self.keypair['name'],
            'security_groups': [{'name': self.security_group['name']}]
        }

        return self.create_server(image=image_ref, flavor=flavor_ref,
                                  create_kwargs=create_kwargs)

    def _get_disk_mount_point_by_size(self, ssh_client, disk_size):
        cmd = ("sudo fdisk -l 2>/dev/null | grep 'Disk /' | "
               "awk '{print substr($2, 6, 4) substr($3, 1, 1)}' | "
               "grep %(disk_size)s | cut -c1-3 | tr -d '\n'"
               % {'disk_size': disk_size})
        try:
            disk_mount_point = self.run_cmd(ssh_client, cmd)
            return disk_mount_point
        except exceptions.SSHExecCommandFailed:
            return None  # disk not found

    def _get_disk_type(self, ssh_client, disk_mount_point):
        # NOTE(abalutoiu): In both cases (SCSI and IDE), disks will be attached
        # as SCSI devices, but the SCSI disk will have QEMU type while the
        # IDE disk will have ATA type
        try:
            cmd = ('sudo dmesg | grep "\[%(disk_mount_point)s] Attached SCSI"'
                   " | awk '{print $4}' | tr -d '\n'"
                   % {'disk_mount_point': disk_mount_point})
            disk_info_code = self.run_cmd(ssh_client, cmd)

            cmd = ("sudo dmesg | grep 'scsi %(disk_info_code)s'"
                   " | awk '{print $6}' | tr -d '\n'"
                   % {'disk_info_code': disk_info_code})
            return self.run_cmd(ssh_client, cmd)
        except exceptions.SSHExecCommandFailed:
            return None

    def check_disk_type(self, ssh_client, disk_size, disk_type):
        disk_mount_point = self._get_disk_mount_point_by_size(ssh_client,
                                                              disk_size)
        if disk_mount_point is None:
            return False

        if disk_type == "scsi":
            disk_expected_type = "QEMU"
        elif disk_type == "ide":
            disk_expected_type = "ATA"
        else:
            raise Exception("Unsupported disk type.")

        if disk_expected_type == self._get_disk_type(ssh_client,
                                                     disk_mount_point):
            return True
        return False

    def check_order_disks(self, ssh_client, disk_size_list=None):
        previous_mount_point = self._get_disk_mount_point_by_size(
            ssh_client, disk_size_list[0])
        for disk_size in disk_size_list[1:]:
            curr_mount_point = self._get_disk_mount_point_by_size(ssh_client,
                                                                  disk_size)
            if previous_mount_point[-1] > curr_mount_point[-1]:
                return False

        return True

    @test.idempotent_id('f6a75c42-fd9b-4f67-89e8-83a3c9eae619')
    @test.services('compute')
    def test_boot_from_volume(self):
        vol_size_bootable = 1

        bd_map_v2_list = self.add_to_bd_map_v2(curr_bd_map_v2=[],
                                               src_type='image',
                                               dest_type='volume',
                                               uuid=self.image_ref,
                                               boot_index=0,
                                               disk_bus='scsi',
                                               volume_size=vol_size_bootable,
                                               delete_on_termination=True)

        instance = self._boot_instance_with_bd_map_v2(
            bd_map_v2=bd_map_v2_list,
            flavor_ref=CONF.compute.flavor_ref)

        fl_ip = self.add_floating_ip(instance=instance)
        ssh_client = self.get_ssh(ip=fl_ip)

        self.assertTrue(self.check_disk_type(ssh_client,
                                             disk_size=vol_size_bootable,
                                             disk_type='scsi'))

    @test.idempotent_id('0b2d48a1-b8d8-47f5-bbd3-d80841e2dd1d')
    @test.services('compute')
    def test_boot_from_volume_with_ephemeral(self):
        if not self.flavor_with_ephemeral:
            raise self.skipTest("flavor_ref should have ephemeral size greater"
                                "than 3 to run this test.")

        vol_size_bootable = 1
        vol_size_ephemeral = 2
        bd_map_v2_list = self.add_to_bd_map_v2(curr_bd_map_v2=[],
                                               src_type='image',
                                               dest_type='volume',
                                               uuid=self.image_ref,
                                               boot_index=0,
                                               disk_bus='ide',
                                               volume_size=vol_size_bootable,
                                               delete_on_termination=True)

        bd_map_v2_list = self.add_to_bd_map_v2(curr_bd_map_v2=bd_map_v2_list,
                                               src_type='blank',
                                               dest_type='local',
                                               boot_index=1,
                                               disk_bus='ide',
                                               volume_size=vol_size_ephemeral)

        instance = self._boot_instance_with_bd_map_v2(
            bd_map_v2=bd_map_v2_list,
            flavor_ref=self.flavor_with_ephemeral)

        fl_ip = self.add_floating_ip(instance=instance)
        ssh_client = self.get_ssh(ip=fl_ip)

        self.assertTrue(self.check_disk_type(ssh_client,
                                             disk_size=vol_size_bootable,
                                             disk_type='ide'))
        self.assertTrue(self.check_disk_type(ssh_client,
                                             disk_size=vol_size_ephemeral,
                                             disk_type='ide'))
        # check disk attach order by using their size
        self.assertTrue(self.check_order_disks(
            ssh_client, disk_size_list=[vol_size_bootable,
                                        vol_size_ephemeral]))

    @test.idempotent_id('c1e0b3c1-9232-455f-9bed-3090d7cd6c11')
    @test.services('compute')
    def test_boot_flavor_without_ephemeral(self):
        if not self.flavor_without_ephemeral:
            raise self.skipTest("flavor_ref_alt should not have ephemeral.")

        vol_size_ephemeral = 1
        vol_size_bootable = 2
        bd_map_v2_list = self.add_to_bd_map_v2(curr_bd_map_v2=[],
                                               src_type='image',
                                               dest_type='volume',
                                               uuid=self.image_ref,
                                               boot_index=0,
                                               disk_bus='scsi',
                                               volume_size=vol_size_bootable,
                                               delete_on_termination=True)

        bd_map_v2_list = self.add_to_bd_map_v2(curr_bd_map_v2=bd_map_v2_list,
                                               src_type='blank',
                                               dest_type='local',
                                               boot_index=1,
                                               disk_bus='ide',
                                               volume_size=vol_size_ephemeral)

        self.assertRaises(exceptions.ServerFault,
                          self._boot_instance_with_bd_map_v2,
                          bd_map_v2=bd_map_v2_list,
                          flavor_ref=self.flavor_without_ephemeral)

    @test.idempotent_id('171f07e5-1577-419c-bafe-ef94d0bd7768')
    @test.services('compute')
    def test_boot_multiple_volumes_attached(self):
        if not self.flavor_with_ephemeral:
            raise self.skipTest("flavor_ref should have ephemeral size greater"
                                "than 3 to run this test.")

        vol_size_bootable = 1
        vol_size_ephemeral = 2
        vol_size_scsi = 3
        bd_map_v2_list = self.add_to_bd_map_v2(curr_bd_map_v2=[],
                                               src_type='image',
                                               dest_type='volume',
                                               uuid=self.image_ref,
                                               boot_index=0,
                                               disk_bus='scsi',
                                               volume_size=vol_size_bootable,
                                               delete_on_termination=True)

        bd_map_v2_list = self.add_to_bd_map_v2(curr_bd_map_v2=bd_map_v2_list,
                                               src_type='blank',
                                               dest_type='local',
                                               boot_index=1,
                                               disk_bus='scsi',
                                               volume_size=vol_size_ephemeral)

        volume = self._create_volume_empty_or_from_image(size=vol_size_scsi)
        bd_map_v2_list = self.add_to_bd_map_v2(curr_bd_map_v2=bd_map_v2_list,
                                               src_type='volume',
                                               dest_type='volume',
                                               uuid=volume['id'],
                                               boot_index=2,
                                               disk_bus='ide',
                                               delete_on_termination=True)

        instance = self._boot_instance_with_bd_map_v2(
            bd_map_v2=bd_map_v2_list,
            flavor_ref=self.flavor_with_ephemeral)

        fl_ip = self.add_floating_ip(instance=instance)
        ssh_client = self.get_ssh(ip=fl_ip)

        self.assertTrue(self.check_disk_type(ssh_client,
                                             disk_size=vol_size_bootable,
                                             disk_type='scsi'))
        self.assertTrue(self.check_disk_type(ssh_client,
                                             disk_size=vol_size_ephemeral,
                                             disk_type='scsi'))
        self.assertTrue(self.check_disk_type(ssh_client,
                                             disk_size=vol_size_scsi,
                                             disk_type='ide'))
        # check disk attach order by using their size
        self.assertTrue(self.check_order_disks(
            ssh_client,
            disk_size_list=[vol_size_bootable,
                            vol_size_ephemeral,
                            vol_size_scsi]))

    def _test_boot_multiple_ephemerals_attached(self, first_disk_type,
                                                second_disk_type):
        if not self.flavor_with_ephemeral:
            raise self.skipTest("flavor_ref should have ephemeral size greater"
                                "than 3 to run this test.")

        vol_size_eph_1 = 1
        vol_size_eph_2 = 2
        bd_map_v2_list = self.add_to_bd_map_v2(curr_bd_map_v2=[],
                                               src_type='blank',
                                               dest_type='local',
                                               boot_index=1,
                                               disk_bus=first_disk_type,
                                               volume_size=vol_size_eph_1)
        bd_map_v2_list = self.add_to_bd_map_v2(curr_bd_map_v2=bd_map_v2_list,
                                               src_type='blank',
                                               dest_type='local',
                                               boot_index=2,
                                               disk_bus=second_disk_type,
                                               volume_size=vol_size_eph_2)

        instance = self._boot_instance_with_bd_map_v2(
            image_ref=self.image_ref,
            bd_map_v2=bd_map_v2_list,
            flavor_ref=self.flavor_with_ephemeral)

        fl_ip = self.add_floating_ip(instance=instance)
        ssh_client = self.get_ssh(ip=fl_ip)

        self.assertTrue(self.check_disk_type(ssh_client,
                                             disk_size=vol_size_eph_1,
                                             disk_type=first_disk_type))
        self.assertTrue(self.check_disk_type(ssh_client,
                                             disk_size=vol_size_eph_2,
                                             disk_type=second_disk_type))
        # check disk attach order by using their size
        self.assertTrue(self.check_order_disks(
            ssh_client,
            disk_size_list=[vol_size_eph_1,
                            vol_size_eph_2]))

    @test.idempotent_id('026eaeae-6594-4dc2-8007-35ec3cbf8290')
    @test.services('compute')
    def test_boot_two_ide_ephemerals(self):
        self._test_boot_multiple_ephemerals_attached(
            first_disk_type='ide',
            second_disk_type='ide')

    @test.idempotent_id('da5e1206-fe31-442d-bfcf-04119fea3769')
    @test.services('compute')
    def test_boot_two_mixt_ephemerals(self):
        self._test_boot_multiple_ephemerals_attached(
            first_disk_type='scsi',
            second_disk_type='ide')
