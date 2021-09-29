import random
from enum import Enum
import sys
# insert at 1, 0 is the script path (or '' in REPL)
sys.path.insert(1, '..\source')
from source import wsnlab_vis as wsn
import math


Roles = Enum('Roles', 'UNDISCOVERED UNREGISTERED ROOT REGISTERED')
"""Enumeration of roles"""

###########################################################
class SensorNode(wsn.Node):
    """SensorNode class is inherited from Node class in wsnlab.py.
    It will run data collection tree construction algorithms.

    Attributes:
        role (Roles): role of node
        is_root_eligible (bool): keeps eligibility to be root
        c_probe (int): probe message counter
        th_probe (int): probe message threshold
        received_HB_addresses (List of Addr): keeps the addresses of received heart beat messages
    """

    ###################
    def init(self):
        """Initialization of node. Setting all attributes of node.
        At the beginning node needs to be sleeping and its role should be UNDISCOVERED.

        Args:

        Returns:

        """
        self.scene.nodecolor(self.id, 1, 1, 1) # sets self color to white
        self.sleep()
        self.addr = None
        self.role = Roles.UNDISCOVERED
        self.is_root_eligible = True if self.id == 0 else False
        self.c_probe = 0  # c means counter and probe is the name of counter
        self.th_probe = 10  # th means threshold and probe is the name of threshold
        self.received_HB_addresses = []  # keeps received HB message addresses

    ###################
    def run(self):
        """Setting the arrival timer to wake up after firing.

        Args:

        Returns:

        """
        self.set_timer('TIMER_ARRIVAL', self.arrival)

    ###################
    def send_probe(self):
        """Sending probe message to be discovered and registered.

        Args:

        Returns:

        """
        self.send({'dest': wsn.BROADCAST_ADDR, 'type': 'PROBE'})

    ###################
    def send_heart_beat(self):
        """Sending heart beat message

        Args:

        Returns:

        """
        self.send({'dest': wsn.BROADCAST_ADDR, 'type': 'HEART_BEAT', 'source': self.addr})

    ###################
    def send_join_request(self, dest):
        """Sending join request message to given destination address to join destination network

        Args:
            dest (Addr): Address of destination node
        Returns:

        """
        self.send({'dest': dest, 'type': 'JOIN_REQUEST', 'gui': self.id})

    ###################
    def send_join_reply(self, gui, addr):
        """Sending join reply message to register the node requested to join.
        The message includes a gui to determine which node will take this reply and an addr to be assigned to the node.

        Args:
            gui (int): Global unique ID
            addr (Addr): Address that will be assigned to new registered node
        Returns:

        """
        self.send({'dest': wsn.BROADCAST_ADDR, 'type': 'JOIN_REPLY', 'source': self.addr, 'gui': gui, 'addr': addr})

    ###################
    def send_join_ack(self, dest):
        """Sending join acknowledgement message to given destination address.

        Args:
            dest (Addr): Address of destination node
        Returns:

        """
        self.send({'dest': dest, 'type': 'JOIN_ACK', 'source': self.addr})

    ###################
    def on_receive(self, pck):
        """Executes when a package received.

        Args:
            pck (Dict): received package
        Returns:

        """
        if self.role == Roles.ROOT:  # if the node is root
            if pck['type'] == 'PROBE':  # it waits and sends heart beat message once received probe message
                yield self.timeout(.5)
                self.send_heart_beat()
            if pck['type'] == 'JOIN_REQUEST':  # it waits and sends join reply message once received join request
                self.log(pck)
                yield self.timeout(.5)
                self.send_join_reply(pck['gui'], wsn.Addr(self.addr.net_addr, pck['gui']))

        elif self.role == Roles.UNDISCOVERED:  # if the node is undiscovered
            if pck['type'] == 'HEART_BEAT':  # it kills probe timer, becomes unregistered and sets join request timer once received heart beat
                self.kill_timer('TIMER_PROBE')
                self.role = Roles.UNREGISTERED
                self.scene.nodecolor(self.id, 1, 1, 0)
                yield self.timeout(.5)
                self.send_probe()
                self.set_timer('TIMER_JOIN_REQUEST', 20)

        if self.role == Roles.UNREGISTERED:  # if the node is unregistered
            if pck['type'] == 'HEART_BEAT':  # it stores the address of the heart beat message once received heart beat
                self.log(pck)
                self.received_HB_addresses.append(pck['source'])
            if pck['type'] == 'JOIN_REPLY':  # it becomes registered and sends join ack if the message is sent to itself once received join reply
                self.log(pck)
                if pck['gui'] == self.id:
                    self.addr = pck['addr']
                    self.role = Roles.REGISTERED
                    self.scene.nodecolor(self.id, 0, 1, 0)
                    self.kill_timer('TIMER_JOIN_REQUEST')
                    yield self.timeout(.5)
                    self.send_join_ack(pck['source'])

    ###################
    def on_timer_fired(self, name, *args, **kwargs):
        """Executes when a timer fired.

        Args:
            name (string): Name of timer.
            *args (string): Additional args.
            **kwargs (string): Additional key word args.
        Returns:

        """
        if name == 'TIMER_ARRIVAL':  # it wakes up and set timer probe once time arrival timer fired
            self.scene.nodecolor(self.id, 1, 0, 0)  # sets self color to red
            self.wake_up()
            self.set_timer('TIMER_PROBE', 1)

        elif name == 'TIMER_PROBE':  # it sends probe if counter didn't reach the threshold once timer probe fired.
            if self.c_probe < self.th_probe:
                self.send_probe()
                self.c_probe += 1
                self.set_timer('TIMER_PROBE', 1)
            else:  # if the counter reached the threshold
                if self.is_root_eligible:  # if the node is root eligible, it becomes root
                    self.role = Roles.ROOT
                    self.scene.nodecolor(self.id, 0, 0, 1)
                    self.addr = wsn.Addr(1, 254)
                    self.set_timer('TIMER_HEART_BEAT', 15)
                else:  # otherwise it keeps trying to sending probe after a long time
                    self.c_probe = 0
                    self.set_timer('TIMER_PROBE', 30)

        elif name == 'TIMER_HEART_BEAT':  # it sends heart beat message once heart beat timer fired
            self.send_heart_beat()
            self.set_timer('TIMER_HEART_BEAT', 15)

        elif name == 'TIMER_JOIN_REQUEST':  # if it has not received heart beat messages before, it sets timer again and wait heart beat messages once join request timer fired.
            if len(self.received_HB_addresses) == 0:
                self.send_probe()
                self.set_timer('TIMER_JOIN_REQUEST', 20)
            else:  # otherwise it chose one of them and sends join request
                self.send_join_request(random.choice(self.received_HB_addresses))
                self.set_timer('TIMER_JOIN_REQUEST', 5)


###########################################################
def create_network(node_class, number_of_nodes=100):
    """Creates given number of nodes at random positions with random arrival times.

    Args:
        node_class (Class): Node class to be created.
        number_of_nodes (int): Number of nodes.
    Returns:

    """
    edge = math.ceil(math.sqrt(number_of_nodes))
    for i in range(number_of_nodes):
        x = i / edge
        y = i % edge
        px = 50 + x * 60 + random.uniform(-20, 20)
        py = 50 + y * 60 + random.uniform(-20, 20)
        node = sim.add_node(node_class, (px, py))
        node.tx_range = 75
        node.logging = True
        node.arrival = random.uniform(0, 50)


sim = wsn.Simulator(
    duration=100, # simulation Duration in seconds
    timescale=0.1, #  The real time dureation of 1 second simualtion time
    visual=True,    # visualization active
    terrain_size=(700, 700),    #terrain size
    title="Data Collection Tree") 

# creating random network
create_network(SensorNode, 100)

# start the simulation
sim.run()

# Created 100 nodes at random locations with random arrival times.
# When nodes are created they appear in white
# Activated nodes becomes red
# Discovered nodes will be yellow
# Registered nodes will be green.
# Root/Routers/Cluster Heads should be blue
