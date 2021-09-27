import random
from enum import Enum
import sys
# insert at 1, 0 is the script path (or '' in REPL)
sys.path.insert(1, '..\source')
from source import wsnlab_vis as wsn

Roles = Enum('Roles', 'UNDISCOVERED UNREGISTERED ROOT')

###########################################################
class SensorNode(wsn.Node):

    ###################
    def init(self):
        self.scene.nodecolor(self.id, .7, .7, .7) # sets self color to grey
        self.addr = None
        self.role = Roles.UNDISCOVERED
        self.is_root_eligible = random.choice([True, False])

    ###################
    def run(self):
        self.n = 0
        self.th_n = 10
        self.set_timer('TIMER_A', 1)

    ###################
    def send_probe(self):
        self.send({'dest': wsn.BROADCAST_ADDR, 'type': 'PROBE'})

    ###################
    def on_receive(self, pck):
        if self.role == Roles.UNDISCOVERED:
            self.kill_timer('TIMER_A')
            self.role = Roles.UNREGISTERED
            self.scene.nodecolor(self.id, 0, 1, 1)

    ###################
    def on_timer_fired(self, name, *args, **kwargs):
        if name == 'TIMER_A':
            if self.n < self.th_n:
                self.send_probe()
                self.n += 1
                self.set_timer('TIMER_A',1)
            else:
                if self.is_root_eligible:
                    self.role = Roles.ROOT
                    self.scene.nodecolor(self.id, 1, 0, 0)
                    self.addr = wsn.Addr(1,254)
                else:
                    self.n = 0
                    self.set_timer('TIMER_A', 30)


    def createNode(self):
        # place node
        node = sim.add_node(SensorNode, (250, 250))
        node.tx_range = 75
        node.logging = True
        return(node)

###########################################################
sim = wsn.Simulator(
    #TODO Create 100 nodes at random locations with random interarrival times. This will be done by creating first node and then schedule
    #     a timer with a random expiration time (less than 10 seconds) 
    #     Node creation should be called by the expiration of the timer  (call the timer Timer_CreateNode)


    until=100,
    timescale=0.1,
    visual=True,
    terrain_size=(700, 700),
    title="Becoming Root Demo")

# place node
# node = sim.add_node(SensorNode, (250, 250))
# node.tx_range = 75
# node.logging = True
node = SensorNode.createNode(SensorNode);

# start the simulation
sim.run()
