import ast
import logging
import random

from ipaddress import ip_address, ip_network

import ptf
import ptf.packet as scapy
import ptf.dataplane as dataplane

from ptf.base_tests import BaseTest
from ptf.testutils import *
from ptf.mask import Mask

class VlanTest(BaseTest):
    def __init__(self):
        '''
        @summary: constructor
        '''
        BaseTest.__init__(self)
        self.test_params = test_params_get()

    #---------------------------------------------------------------------

    def setUp(self):
        '''
        @summary: Setup for the test
        Two test parameters are used:
         - dst_ip: the IP address to creat the eth_ip of the packets
         - dst_mac: the MAC address to create the eth_dst of the packet
         - src_port: src_port will send packets
         - dst_ports: dst_port list will receive packets
         - pkt_action: expect to 'receive' or 'not receive' the packets.'fwd' is expect to 'receive'
         - strip_vlan: set to 'True' if input tagged pkt and expect to receive untagged pkt
        '''
        self.dataplane            = ptf.dataplane_instance
        self.src_mac              = self.test_params['src_mac']
        self.dst_mac              = self.test_params['dst_mac']
        self.src_port             = self.test_params['src_port']
        self.dst_ports            = self.test_params['dst_ports']
        self.vlan                 = int(self.test_params.get('vlan', 0))
        self.strip_vlan           = self.test_params.get('strip_vlan', False)
        self.dst_ip               = self.test_params.get('dst_ip', None)
        self.pkt_action           = self.test_params.get('pkt_action', 'fwd')
        self.pkt_type             = 'L3' if self.dst_ip else 'L2'

    #---------------------------------------------------------------------

    def check_traffic(self):
            
        (pkt, masked_exp_pkt) = self.generate_packet()

        send_packet(self, self.src_port, pkt)
        logging.info(
            "\n Sending packet with ip {} from port {} to {}".format(self.dst_ip, self.src_port, self.dst_ports) + \
            "\n packet_action : {}".format(self.pkt_action) + \
            "\n pkt_type: {}".format(self.pkt_type)
        )

        if self.pkt_action == 'fwd':
            (matched_index, received) = verify_packet_any_port(self, masked_exp_pkt, self.dst_ports)
            assert received
        else:
            verify_no_packet_any(self, masked_exp_pkt, self.dst_ports)

    #---------------------------------------------------------------------

    def generate_packet(self):
        if self.pkt_type == 'L2':
            pkt           =  simple_ip_packet( 
                                pktlen=100 if self.vlan == 0 else 104,
                                eth_dst=self.dst_mac,
                                eth_src=self.src_mac,
                                dl_vlan_enable=False if self.vlan == 0  else True,
                                vlan_vid=self.vlan,
                                ip_src='10.0.0.1',
                                ip_dst='192.168.0.2'
                            )
            untagged_pkt  = simple_ip_packet(
                                pktlen=100,
                                eth_dst=self.dst_mac,
                                eth_src=self.src_mac,
                                dl_vlan_enable=False,
                                vlan_vid=self.vlan,
                                ip_src='10.0.0.1',
                                ip_dst='192.168.0.2'
                            )
            masked_exp_pkt = Mask(untagged_pkt) if self.vlan != 0 and self.strip_vlan else Mask(pkt)
        else:
            sport   = random.randint(0, 65535)
            dport   = random.randint(0, 65535)
            src_mac = self.dataplane.get_mac(0, 0)

            version = ip_network(unicode(self.dst_ip)).version
            ip_src  = "10.0.0.1" if version == 4 else "2000::1"
            ip_dst  = self.dst_ip

            pkt_args     = {
                'eth_src':   src_mac,
                'eth_dst':   self.dst_mac,
                'tcp_sport': sport,
                'tcp_dport': dport
            }

            exp_pkt_args = {
                'eth_src':   self.dst_mac,
                'tcp_sport': sport,
                'tcp_dport': dport
            }

            if version == 4:
                pkt_args.update({'ip_src': ip_src, 'ip_dst': ip_dst, 'ip_ttl': 64})
                exp_pkt_args.update({'ip_src': ip_src, 'ip_dst': ip_dst, 'ip_ttl': 63})
                pkt     = simple_tcp_packet(**pkt_args)
                exp_pkt = simple_tcp_packet(**exp_pkt_args)
            else:
                pkt_args.update({'ipv6_src': ip_src, 'ipv6_dst': ip_dst, 'ipv6_hlim': 64})
                exp_pkt_args.update({'ipv6_src': ip_src, 'ipv6_dst': ip_dst, 'ipv6_hlim': 63})
                pkt     = simple_tcpv6_packet(**pkt_args)
                exp_pkt = simple_tcpv6_packet(**exp_pkt_args)

            masked_exp_pkt = Mask(exp_pkt)
            masked_exp_pkt.set_do_not_care_scapy(scapy.Ether, "dst")

        return (pkt, masked_exp_pkt)

    # ---------------------------------------------------------------------

    def runTest(self):
        self.check_traffic()