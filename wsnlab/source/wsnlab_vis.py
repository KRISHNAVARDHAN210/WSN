"""Visualisation of wsnlab library. Basen on wsnsimpy_tk. Used package instead of message by Mustafa Tosun.
"""
import sys
# insert at 1, 0 is the script path (or '' in REPL)
sys.path.insert(1, '..')
from wsnlab.source import wsnlab
from wsnlab.source.wsnlab import *
from threading import Thread
from wsnlab.topovis import Scene
from wsnlab.topovis.TkPlotter import Plotter


class Node(wsnlab.Node):
    """Class to model a visualised network node inherited wsnlab.Node.

       Attributes:
           scene (Scene): Scene object to visualise

    """

    ###################
    def __init__(self, sim, id, pos):
        """Constructor for visualised Node class. Creates a node in topovis scene.

           Args:
               sim (Simulator): Simulation environment of node.
               id (int): Global unique ID of node.
               pos (Tuple(double,double)): Position of node.

           Returns:
               Node: Created node object.
        """
        super().__init__(sim, id, pos)
        self.scene = self.sim.scene
        self.scene.node(id, *pos)

    ###################
    def send(self, pck):
        """Visualise sending process in addition to base send method.

           Args:
               pck (Dict): Package to be sent.

           Returns:

        """
        obj_id = self.scene.circle(
            self.pos[0], self.pos[1],
            self.tx_range,
            line="wsnsimpy:tx")
        super().send(pck)
        self.delayed_exec(0.2, self.scene.delshape, obj_id)
        # When unicast is added, it needs to be re-arranged
        # if not pck['dest'].is_equal(wsnlab.BROADCAST_ADDR):
        #     destPos = self.sim.nodes[pck['dest'].l].pos
        #     obj_id = self.scene.line(
        #         self.pos[0], self.pos[1],
        #         destPos[0], destPos[1],
        #         line="wsnsimpy:unicast")
        #     self.delayed_exec(0.2,self.scene.delshape,obj_id)

    ###################
    def move(self, x, y):
        """Visualise move process in addition to base move method.

           Args:
               x (double): x of position.
               y (double): y of position.

           Returns:

        """
        super().move(x, y)
        self.scene.nodemove(self.id, x, y)


###########################################################
class _FakeScene:
    def _fake_method(self, *args, **kwargs):
        pass

    def __getattr__(self, name):
        return self._fake_method


###########################################################


###########################################################
class Simulator(wsnlab.Simulator):
    '''Wrap WsnSimPy's Simulator class so that Tk main loop can be started in the
    main thread

    Attributes:
        visual (bool): A flag to visualising process.
        terrain_size (Tuple(double,double)): Size of visualised terrain.
    '''

    def __init__(self, duration, timescale=1, seed=0, terrain_size=(500, 500), visual=True, title=None):
        """Constructor for visualised Simulator class.

           Args:
               duration (double): Duration of simulation.
               timescale (double): Seconds in real time for 1 second in simulation. It arranges speed of simulation
               seed (double): seed for Random bbject.
               terrain_size (Tuple(double,double)): Size of visualised terrain.
               visual (bool): A flag to visualising process.
               title (string): Title of scene.

           Returns:
               Simulator: Created Simulator object.
        """
        super().__init__(duration, timescale, seed)
        self.visual = visual
        self.terrain_size = terrain_size
        if self.visual:
            self.scene = Scene(realtime=True)
            self.scene.linestyle("wsnsimpy:tx", color=(0, 0, 1), dash=(5, 5))
            self.scene.linestyle("wsnsimpy:ack", color=(0, 1, 1), dash=(5, 5))
            self.scene.linestyle("wsnsimpy:unicast", color=(0, 0, 1), width=3, arrow='head')
            self.scene.linestyle("wsnsimpy:collision", color=(1, 0, 0), width=3)
            if title is None:
                title = "WsnSimPy"
            self.tkplot = Plotter(windowTitle=title, terrain_size=terrain_size)
            self.tk = self.tkplot.tk
            self.scene.addPlotter(self.tkplot)
            self.scene.init(*terrain_size)
        else:
            self.scene = _FakeScene()

    def _update_time(self):
        """Updates time in scene.

           Args:

           Returns:
        """
        while True:
            self.scene.setTime(self.now)
            yield self.timeout(0.1)

    def run(self):
        """Starts visualisation process. Puts base run method to a Thread so that visualisation become main process.

           Args:

           Returns:
        """
        if self.visual:
            self.env.process(self._update_time())
            thr = Thread(target=super().run)
            thr.setDaemon(True)
            thr.start()
            self.tkplot.tk.mainloop()
        else:
            super().run()
