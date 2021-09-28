"""Simulator library for self-organizing ad hoc networks.
Based on wsnsimpy library. Timers, Network address and Sleep mode are included by Mustafa Tosun.
"""

import bisect
import inspect
import random
import simpy
from simpy.util import start_delayed

###########################################################
class Addr:
    """Use for a network address which has two parts

       Attributes:
           f (int): First part of the address.
           l (int): Last part of the address.
    """

    ############################
    def __init__(self, net_addr, node_addr):
        """Constructor for Addr class.

           Args:
               f (int): First part of the address.
               l (int): Last part of the address.

           Returns:
               Addr: Created Addr object.
        """
        self.net_addr = net_addr
        self.node_addr = node_addr

    ############################
    def __eq__(self, other):
        """ == operator function for Addr objects.

           Args:
               other (Addr): An Addr object to compare.

           Returns:
               bool: returns True if the objects are equal, otherwise False.
        """
        if self.net_addr == other.net_addr and self.node_addr == other.node_addr:
            return True
        return False

    ############################
    def is_equal(self, other):
        """Comparison function for Addr objects.

           Args:
               other (Addr): An Addr object to compare.

           Returns:
               bool: returns True if the objects are equal, otherwise False.
        """
        if self.net_addr == other.net_addr and self.node_addr == other.node_addr:
            return True
        return False


BROADCAST_ADDR = Addr(255, 255)
"""Addr: Keeps broadcast address.
"""



###########################################################
def ensure_generator(env, func, *args, **kwargs):
    '''
    Make sure that func is a generator function.  If it is not, return a
    generator wrapper
    '''
    if inspect.isgeneratorfunction(func):
        return func(*args, **kwargs)
    else:
        def _wrapper():
            func(*args, **kwargs)
            yield env.timeout(0)

        return _wrapper()


###########################################################
def distance(pos1, pos2):
    """Calculates the distance between two positions.

       Args:
           pos1 (Tuple(double,double)): First position.
           pos2 (Tuple(double,double)): Second position.

       Returns:
           double: returns the distance between two positions.
    """
    return ((pos1[0] - pos2[0]) ** 2 + (pos1[1] - pos2[1]) ** 2) ** 0.5


###########################################################
class Node:
    """Class to model a network node with basic operations. It's base class for more complex node classes.

       Attributes:
           pos (Tuple(double,double)): Position of node.
           tx_range (double): Transmission range of node.
           sim (Simulator): Simulation environment of node.
           id (int): Global unique ID of node.
           addr (Addr): Network address of node.
           is_sleep (bool): If it is True, It means node is sleeping and can not receive messages.
           Otherwise, node is awaken.
           logging (bool): It is a flag for logging. If it is True, nodes outputs can be seen in terminal.
           active_timer_list (List of strings): It keeps the names of active timers.
           neighbor_distance_list (List of Tuple(double,int)): Sorted list of nodes distances to other nodes.
            Each Tuple keeps a distance and a node id.
           timeout (Function): timeout function

    """

    ############################
    def __init__(self, sim, id, pos):
        """Constructor for base Node class.

           Args:
               sim (Simulator): Simulation environment of node.
               id (int): Global unique ID of node.
               pos (Tuple(double,double)): Position of node.

           Returns:
               Node: Created node object.
        """
        self.pos = pos
        self.tx_range = 0
        self.sim = sim
        self.id = id
        self.addr = Addr(0, id)
        self.is_sleep = False
        self.logging = True
        self.active_timer_list = []
        self.neighbor_distance_list = []
        self.timeout = self.sim.timeout

    ############################
    def __repr__(self):
        """Representation method of Node.

           Args:

           Returns:
               string: represents Node object as a string.
        """
        return '<Node %d:(%.2f,%.2f)>' % (self.id, self.pos[0], self.pos[1])

    ############################
    @property
    def now(self):
        """Property for time of simulation.

           Args:

           Returns:
               double: Time of simulation.
        """
        return self.sim.env.now

    ############################
    def log(self, msg):
        """Writes outputs of node to terminal.

           Args:
                msg (string): Output text
           Returns:

        """
        if self.logging:
            print(f"Node {'#' + str(self.id):4}[{self.now:10.5f}] {msg}")

    ############################
    def send(self, pck):
        """Sends given package. If dest address in pck is broadcast address, it sends the package to all neighbors.

           Args:
                pck (Dict): Package to be sent. It should contain 'dest' which is destination address.
           Returns:

        """
        for (dist, node) in self.neighbor_distance_list:
            if dist <= self.tx_range:
                if pck['dest'].is_equal(BROADCAST_ADDR) or pck['dest'].is_equal(node.addr):
                    prop_time = dist / 1000000 - 0.00001
                    self.delayed_exec(prop_time, node.on_receive_check, pck)
            else:
                break

    ############################
    def set_timer(self, name, time, *args, **kwargs):
        """Sets a timer with a given name. It appends name of timer to the active timer list.

           Args:
                name (string): Name of timer.
                time (double): Duration of timer.
                *args (string): Additional args.
                **kwargs (string): Additional key word args.
           Returns:

        """
        self.active_timer_list.append(name)
        self.delayed_exec(time - 0.00001, self.on_timer_fired_check, name, *args, **kwargs)

    ############################
    def kill_timer(self, name):
        """Kills a timer with a given name. It removes name of timer from the active timer list if exists.

           Args:
                name (string): Name of timer.
           Returns:

        """
        if name in self.active_timer_list:
            self.active_timer_list.remove(name)

    ############################
    def delayed_exec(self, delay, func, *args, **kwargs):
        """Executes a function with given parameters after a given delay.

           Args:
                delay (double): Delay duration.
                func (Function): Function to execute.
                *args (double): Function args.
                delay (double): Function key word args.
           Returns:

        """
        return self.sim.delayed_exec(delay, func, *args, **kwargs)

    ############################
    def init(self):
        """Initialize a node. It is executed at the beginning of simulation. It should be overridden if needed.

           Args:

           Returns:

        """
        pass

    ############################
    def run(self):
        """Run method of a node. It is executed after init() at the beginning of simulation.
        It should be overridden if needed.

           Args:

           Returns:

        """
        pass

    ###################
    def move(self, x, y):
        """Moves a node from the current position to given position

           Args:
               x (double): x of position.
               y (double): y of position.
.
           Returns:
         """
        self.pos = (x, y)
        self.sim.update_neighbor_list(self.id)

    ############################
    def on_receive(self, pck):
        """It is executed when node receives a package. It should be overridden if needed.

           Args:
                pck (Dict): Package received
           Returns:

        """
        pass

    ############################
    def on_receive_check(self, pck):
        """Checks if node is sleeping or not for incoming package.
        If sleeping, does not call on_recieve() and does not receive package.

           Args:
                pck (Dict): Incoming package
           Returns:

        """
        if not self.is_sleep:
            self.delayed_exec(0.00001, self.on_receive, pck)

    ############################
    def on_timer_fired(self, name, *args, **kwargs):
        """It is executed when a timer fired. It should be overridden if needed.

           Args:
                name (string): Name of timer.
                *args (string): Additional args.
                **kwargs (string): Additional key word args.
           Returns:

        """
        pass

    ############################
    def on_timer_fired_check(self, name, *args, **kwargs):
        """Checks if the timer about to fire is in active timer list or not. If not, does not call on_timer_fired().

           Args:
                name (string): Name of timer.
                *args (string): Additional args.
                **kwargs (string): Additional key word args.
           Returns:

        """
        if name in self.active_timer_list:
            self.active_timer_list.remove(name)
            self.delayed_exec(0.00001, self.on_timer_fired, name, *args, **kwargs)

    ############################
    def sleep(self):
        """Make node sleep. In sleeping node can not receive packages.

           Args:

           Returns:

        """
        self.is_sleep = True

    ############################
    def wake_up(self):
        """Wake node up to receive incoming messages.

           Args:

           Returns:

        """
        self.is_sleep = False

    ############################
    def finish(self):
        """It is executed at the end of simulation. It should be overridden if needed.

           Args:

           Returns:

        """
        pass


###########################################################
class Simulator:
    """Class to model a network.

       Attributes:
           timescale (double): Seconds in real time for 1 second in simulation. It arranges speed of simulation
           nodes (List of Node): Nodes in network.
           duration (double): Duration of simulation.
           random (Random): Random object to use.
           timeout (Function): Timeout Function.

    """

    ############################
    def __init__(self, duration, timescale=1, seed=0):
        """Constructor for Simulator class.

           Args:
               until (double): Duration of simulation.
               timescale (double): Seconds in real time for 1 second in simulation. It arranges speed of simulation
               seed (double): seed for Random bbject.

           Returns:
               Simulator: Created Simulator object.
        """
        self.env = simpy.rt.RealtimeEnvironment(factor=timescale, strict=False)
        self.nodes = []
        self.duration = duration
        self.timescale = timescale
        self.random = random.Random(seed)
        self.timeout = self.env.timeout

    ############################
    @property
    def now(self):
        """Property for time of simulation.

           Args:

           Returns:
               double: Time of simulation.
        """
        return self.env.now

    ############################
    def delayed_exec(self, delay, func, *args, **kwargs):
        """Executes a function with given parameters after a given delay.

           Args:
                delay (double): Delay duration.
                func (Function): Function to execute.
                *args (double): Function args.
                delay (double): Function key word args.
           Returns:

        """
        func = ensure_generator(self.env, func, *args, **kwargs)
        start_delayed(self.env, func, delay=delay)

    ############################
    def add_node(self, node_class, pos, *args, **kwargs):
        """Adds a new node in to network.

           Args:
                nodeclass (Class): Node class inherited from Node.
                pos (Tuple(double,double)): Position of node.
           Returns:
                nodeclass object: Created nodeclass object
        """
        id = len(self.nodes)
        node = node_class(self, id, pos)
        self.nodes.append(node)
        self.update_neighbor_list(id)
        return node

    ############################
    def update_neighbor_list(self, id):
        '''
        Maintain each node's neighbor list by sorted distance after affected
        by addition or relocation of node with ID id

        Args:
            id (int): Global unique id of node
        Returns:

        '''
        me = self.nodes[id]

        # (re)sort other nodes' neighbor lists by distance
        for n in self.nodes:
            # skip this node
            if n is me:
                continue

            nlist = n.neighbor_distance_list

            # remove this node from other nodes' neighbor lists
            for i, (dist, neighbor) in enumerate(nlist):
                if neighbor is me:
                    del nlist[i]
                    break

            # then insert it while maintaining sort order by distance
            bisect.insort(nlist, (distance(n.pos, me.pos), me))

        self.nodes[id].neighbor_distance_list = [
            (distance(n.pos, me.pos), n)
            for n in self.nodes if n is not me
        ]
        self.nodes[id].neighbor_distance_list.sort()

    ############################
    def run(self):
        """Runs the simulation. It initialize every node, then executes each nodes run function.
        Finally calls finish functions of nodes.

           Args:

           Returns:

        """
        for n in self.nodes:
            n.init()
        for n in self.nodes:
            self.env.process(ensure_generator(self.env, n.run))
        self.env.run(until=self.duration)
        for n in self.nodes:
            n.finish()
