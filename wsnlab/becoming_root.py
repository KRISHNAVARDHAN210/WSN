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


###########################################################
def createNetwork(sensorNode, numberofNodes = 10):
    # Create number of nodes with randiim interarrival times 
    for i in range(numberofNodes):    
        node = sim.add_node(SensorNode, (250, 250))
        node.tx_range = 75
        node.logging = True


sim = wsn.Simulator(
    #TODO Create 100 nodes at random locations with random interarrival times.  You can create all nodes at the beginnning and activate nodes at random times
    # When nodes are created they appear in gray
    # Activated nodes becomes red 
    # Connected nodes will be green.
    # Routers/Cluster Heads should be blue  


    until=100, # simulation Duration in seconds
    timescale=0.1, #  The real time dureation of 1 second simualtion time 
    visual=True,    # visualization active
    terrain_size=(700, 700),    #terrain size
    title="Data Collection Tree") 

# place node
# node = sim.add_node(SensorNode, (250, 250))
# node.tx_range = 75
# node.logging = True


# start the simulation
sim.run()
