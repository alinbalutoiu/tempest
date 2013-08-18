# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2013 OpenStack Foundation
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

from tempest.api.network import base
from tempest.common.utils.data_utils import rand_name
from tempest.test import attr


class RoutersTest(base.BaseNetworkTest):
    _interface = 'json'

    @classmethod
    def setUpClass(cls):
        super(RoutersTest, cls).setUpClass()

    def _delete_router(self, router_id):
        resp, _ = self.client.delete_router(router_id)
        self.assertEqual(204, resp.status)
        # Asserting that the router is not found in the list
        # after deletion
        resp, list_body = self.client.list_routers()
        self.assertEqual('200', resp['status'])
        routers_list = list()
        for router in list_body['routers']:
            routers_list.append(router['id'])
        self.assertNotIn(router_id, routers_list)

    def _remove_router_interface_with_subnet_id(self, router_id, subnet_id):
        resp, _ = self.client.remove_router_interface_with_subnet_id(
            router_id, subnet_id)
        self.assertEqual('200', resp['status'])

    def _remove_router_interface_with_port_id(self, router_id, port_id):
        resp, _ = self.client.remove_router_interface_with_port_id(
            router_id, port_id)
        self.assertEqual('200', resp['status'])

    @attr(type='smoke')
    def test_create_show_list_update_delete_router(self):
        # Create a router
        name = rand_name('router-')
        resp, create_body = self.client.create_router(
            name, external_gateway_info={
                "network_id": self.network_cfg.public_network_id},
            admin_state_up=False)
        self.assertEqual('201', resp['status'])
        self.addCleanup(self._delete_router, create_body['router']['id'])
        self.assertEqual(create_body['router']['name'], name)
        self.assertEqual(
            create_body['router']['external_gateway_info']['network_id'],
            self.network_cfg.public_network_id)
        self.assertEqual(create_body['router']['admin_state_up'], False)
        # Show details of the created router
        resp, show_body = self.client.show_router(
            create_body['router']['id'])
        self.assertEqual('200', resp['status'])
        self.assertEqual(show_body['router']['name'], name)
        self.assertEqual(
            show_body['router']['external_gateway_info']['network_id'],
            self.network_cfg.public_network_id)
        self.assertEqual(show_body['router']['admin_state_up'], False)
        # List routers and verify if created router is there in response
        resp, list_body = self.client.list_routers()
        self.assertEqual('200', resp['status'])
        routers_list = list()
        for router in list_body['routers']:
            routers_list.append(router['id'])
        self.assertIn(create_body['router']['id'], routers_list)
        # Update the name of router and verify if it is updated
        updated_name = 'updated ' + name
        resp, update_body = self.client.update_router(
            create_body['router']['id'], name=updated_name)
        self.assertEqual('200', resp['status'])
        self.assertEqual(update_body['router']['name'], updated_name)
        resp, show_body = self.client.show_router(
            create_body['router']['id'])
        self.assertEqual(show_body['router']['name'], updated_name)

    @attr(type='smoke')
    def test_add_remove_router_interface_with_subnet_id(self):
        network = self.create_network()
        subnet = self.create_subnet(network)
        name = rand_name('router-')
        resp, create_body = self.client.create_router(name)
        self.addCleanup(self.client.delete_router, create_body['router']['id'])
        # Add router interafce with subnet id
        resp, interface = self.client.add_router_interface_with_subnet_id(
            create_body['router']['id'], subnet['id'])
        self.assertEqual('200', resp['status'])
        self.addCleanup(self._remove_router_interface_with_subnet_id,
                        create_body['router']['id'], subnet['id'])
        self.assertTrue('subnet_id' in interface.keys())
        self.assertTrue('port_id' in interface.keys())
        # Verify router id is equal to device id in port details
        resp, show_port_body = self.client.show_port(
            interface['port_id'])
        self.assertEqual(show_port_body['port']['device_id'],
                         create_body['router']['id'])

    @attr(type='smoke')
    def test_add_remove_router_interface_with_port_id(self):
        network = self.create_network()
        self.create_subnet(network)
        name = rand_name('router-')
        resp, create_body = self.client.create_router(name)
        self.addCleanup(self.client.delete_router, create_body['router']['id'])
        resp, port_body = self.client.create_port(network['id'])
        # add router interface to port created above
        resp, interface = self.client.add_router_interface_with_port_id(
            create_body['router']['id'], port_body['port']['id'])
        self.assertEqual('200', resp['status'])
        self.addCleanup(self._remove_router_interface_with_port_id,
                        create_body['router']['id'], port_body['port']['id'])
        self.assertTrue('subnet_id' in interface.keys())
        self.assertTrue('port_id' in interface.keys())
        # Verify router id is equal to device id in port details
        resp, show_port_body = self.client.show_port(
            interface['port_id'])
        self.assertEqual(show_port_body['port']['device_id'],
                         create_body['router']['id'])
