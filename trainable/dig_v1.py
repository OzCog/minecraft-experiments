"""
cliff-walking environment and agent
"""
import cv2
import logging
import random
import time
import torch
from torch import nn
import os
from mcdemoaux.vision import network
import numpy
import copy
from collections import deque
import tagilmo.utils.mission_builder as mb
from tagilmo.utils.vereya_wrapper import MCConnector
from utils import common
from utils.common import stop_motion
from mcdemoaux.vision.network import QVisualNetwork

BLOCK_TYPE = 0
HEIGHT = 2
DIST = -1

class QVisualNetworkV2(QVisualNetwork):
    def __init__(self, n_prev_images, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.n_prev_images = n_prev_images
        num = kwargs.get('num', 128)
        num1 = num * 2
        # fully connected
        self.q_value = nn.Sequential(
            nn.Linear(num + (28 * 8 * 8 + 28 * 4 * 4 + 28) * 3, num1),
            self.activation,
            nn.Linear(num1, num1),
            self.activation,
            nn.Linear(num1, num1),
            self.activation,
            nn.Linear(num1, self.n_actions))
        self.residual = False


    def forward(self, data):
        """

        Parameters:
        data: dict
            expected keys 'image' - tensor of images (batch, T, channels, height, width)
            'state' - vector
        """
        x = data['images'].to(next(self.conv1a.parameters()))
        if len(x.shape) == 4:
            x = x.unsqueeze(0)

        # images are stacked along channel dimention,
        # they need to be translated to batch dimention
        B, T, C, H, W = x.shape
        x = self.vgg(x.view(-1, C, H, W))
        visual_data = self.pooling(x).view(B, T, -1)

        state = data['state']
        if len(state.shape) == 1:
            state = state.unsqueeze(0)

        state_data = state.to(next(self.conv1a.parameters()))
        # yaws + dists + heights + actions
        angle_emb = common.angle_embed(state_data[:, :3])
        dist_emb = common.dist_embed(state_data[:, 3:6])
        state_data = torch.cat([angle_emb, dist_emb, state_data[:, 6:]], dim=1)
        state_emb = self.pos_emb(state_data)
        visual_pos_emb = torch.cat([visual_data.view(B, -1), state_emb], dim=1)
        result = self.q_value(visual_pos_emb)
        if torch.isnan(result).any().item():
            import pdb;pdb.set_trace()

        return result



class DeadException(RuntimeError):
    def __init__(self, arg="it's dead"):
        super().__init__(arg)


# quit by reaching target or when zero health
mission_ending = """
<MissionQuitCommands quitDescription="give_up"/>
<RewardForMissionEnd>
  <Reward description="give_up" reward="243"/>
</RewardForMissionEnd>
"""


def visualize(yaw, dist):
    import cv2
    font = cv2.FONT_HERSHEY_SIMPLEX
    bottomLeftCornerOfText = (40,40)
    fontScale              = 0.9
    fontColor              = (15, 15, 15)
    lineType               = 2
    img_draw = (img * 255).astype(numpy.uint8)
    img_draw = cv2.putText(img_draw.transpose(1,2,0), 'distance {0:.1f}'.format(dist),bottomLeftCornerOfText, font,fontScale,fontColor,lineType)
    #img_draw = cv2.putText(img_draw, 'yaw {0:.1f}'.format(yaw),
    #                       (10, 80),
    #                       font,fontScale,fontColor,lineType)
    c_x = 260
    c_y = 200
    r = 20
    img_draw = cv2.circle(img_draw,
                          (c_x, c_y),
                          r,
                          (0,255,255), 2)
    cos_x = numpy.cos(yaw + numpy.pi / 2) * r
    sin_y = numpy.sin(yaw + numpy.pi / 2) * r
    img_draw = cv2.line(img_draw,
                       (c_x, c_y),
                       (round(c_x - cos_x),
                        round(c_y - sin_y)), (0, 255, 255), 2)
    cv2.imwrite('episodes/img{0}.png'.format(self.img_num), img_draw)
    self.img_num += 1
    #cv2.imshow('1', img_draw)
    #cv2.waitKey(100)


def load_agent(path):
    # possible actions are
    # move[-1, 1],
    # strafe[-1, 1]
    # pitch[-1, 1]
    # turn[-1, 1]
    # jump 0/1

    # for example:
    # actionSet = [network.ContiniousAction('move', -1, 1),
    #              network.ContiniousAction('strafe', -1, 1),
    #              network.ContiniousAction('pitch', -1, 1),
    #              network.ContiniousAction('turn', -1, 1),
    #              network.BinaryAction('jump')]

    # discreet actions
    action_names = ["turn 0.1", "turn -0.1", "pitch 0.015", "pitch -0.015", "move 0.9", "jump_forward", "attack 1"]
    actionSet = [network.CategoricalAction(action_names)]

    policy_net = QVisualNetworkV2(3, actionSet, 0, 32,  n_channels=3, activation=nn.LeakyReLU(), batchnorm=False, num=256)
    target_net = QVisualNetworkV2(3, actionSet, 0, 32,  n_channels=3, activation=nn.LeakyReLU(), batchnorm=False, num=256)
    batch_size = 18

    transformer = common.make_noisy_transformers()
    my_simple_agent = network.DQN(policy_net, target_net, 0.99, batch_size, 450, capacity=7000, transform=transformer)
    location = 'cuda' if torch.cuda.is_available() else 'cpu'
    if os.path.exists(path):
        logging.info('loading model from %s', path)
        data = torch.load(path, map_location=location)
        my_simple_agent.load_state_dict(data, strict=False)

    return my_simple_agent.to(location)


def iterative_avg(current, new):
    return current + 0.01 * (new - current)


def inverse_priority_sample(weights: numpy.array):
    weights = weights - weights.min() + 0.0001
    r = numpy.random.random(len(weights))
    w_new = r ** (1 / weights)
    idx = numpy.argmin(w_new)
    return idx



class Trainer(common.Trainer):
    want_depth = False

    def __init__(self, agent, mc, optimizer, eps, train=True):
        super().__init__(train)
        self.from_queue = False
        self.write_visualization = False
        self.agent = agent
        self.mc = mc
        self.optimizer = optimizer
        logging.info('start eps %f', eps)
        self.eps = eps
        self.img_num = 0
        self.state_queue = deque(maxlen=2)

    def _random_turn(self):
        turn = numpy.random.random() * random.choice([-1, 1])
        self.act(["turn {0}".format(turn)])
        self.act(["pitch {0}".format(0.1)])
        time.sleep(0.5)
        stop_motion(self.mc)

    def collect_visible(self, data, coords1):
        visible = self.mc.getLineOfSight('type')
        if visible is not None:
            coords = [self.mc.getLineOfSight('x'),
                      self.mc.getLineOfSight('y'),
                      self.mc.getLineOfSight('z')]
            height = 1.6025
            coords1[1] += height
            dist = numpy.linalg.norm(numpy.asarray(coords) - numpy.asarray(coords1), 2)
            data['visible'] = [visible] + coords + [dist]

    def collect_state(self):
        mc = self.mc
        mc.observeProc()
        aPos = mc.getAgentPos()
        img_data = self.mc.getImage()
        logging.debug(aPos)
        while aPos is None or (img_data is None):
            time.sleep(0.05)
            mc.observeProc()
            aPos = mc.getAgentPos()
            img_data = self.mc.getImage()
            if not all(mc.isAlive):
                raise DeadException()

        # target
        xpos, ypos, zpos = aPos[0:3]
        logging.debug("%.2f %.2f %.2f ", xpos, ypos, zpos)
        # use relative height
        ypos = 30 - aPos[1]
        data = dict()

        img_data = img_data.reshape((240 * 4, 320 * 4, 3 + self.want_depth))
        img_data = cv2.resize(img_data, (320, 240))
        img = img_data.reshape((240, 320, 3 + self.want_depth)).transpose(2, 0, 1) / 255.
        img = torch.as_tensor(img).float()
        data['image'] = img

        actions = []
        imgs = [torch.as_tensor(img)]
        heights = [torch.as_tensor(ypos)]
        # first prev, then prev_prev etc..
        for item in reversed(self.state_queue):
            actions.append(item['action'])
            imgs.append(item['image'])
            heights.append(item['ypos'])
        while len(imgs) < 3:
            imgs.append(img.to(img))
            actions.append(torch.as_tensor(-1).to(img))
            heights.append(torch.as_tensor(ypos).to(img))
        state = torch.as_tensor(heights + actions)
        data.update(dict(state=state,
                         images=torch.stack(imgs),
                         image=img,
                         ypos=torch.as_tensor(ypos)
                         ))

        # depth
        coords1 = self.collect_visible(data, aPos[:3])
        if self.write_visualization:
            visualize(yaw, dist)
        for key, value in data.items():
            if isinstance(value, torch.Tensor):
                assert not torch.isnan(value).any()
        return data

    def _end(self):
        mean_loss = numpy.mean([self.learn(self.agent, self.optimizer) for _ in range(5)])
        logging.info('mean loss %f', mean_loss)


    def run_episode(self):
        """ Deep Q-Learning episode
        """
        self.agent.clear_state()
        max_t = 250
        self._random_turn()

        mc = self.mc
        logging.debug('memory: %i', self.agent.memory.position)
        self.agent.train()

        eps_start = self.eps
        eps_end = 0.05
        eps_decay = 0.999

        eps = eps_start

        total_reward = 0

        t = 0

        state = self.collect_state()
        if state['ypos'] < 0:
            raise DeadException('started in a pit')
        # pitch, yaw, xpos, ypos, zpos
        prev_pos = None
        prev_target_dist = None
        prev_life = 20
        solved = False

        while True:
            t += 1
            logging.debug('\n\n\nstep %i', t)
            # target = search4blocks(mc, ['lapis_block'], run=False)
            reward = 0
            try:
                data = self.collect_state()
            except DeadException:
                stop_motion(mc)
                self.agent.push_final(-100)
                reward = -100
                logging.debug("failed at step %i", t)
                import pdb;pdb.set_trace()
                self.learn(self.agent, self.optimizer)
                break
            if self.state_queue:
                life = mc.getLife()
                logging.debug('current life %f', life)
                if life == 0:
                    reward = -100
                    stop_motion(mc)
                    if t > 2:
                        self.agent.push_final(reward)
                    self.learn(self.agent, self.optimizer)
                    break
                if 'visible' in self.state_queue[-1]:
                    prev_action = self.agent.policy_net.actions[0].to_string(self.state_queue[-1]['action'])
                    prev_item = self.state_queue[-1]['visible']
                    logging.debug(prev_item)
                    prev_dist = prev_item[DIST]
                    prev_block = prev_item[BLOCK_TYPE]
                    if prev_block in ('water', 'lava', 'flowing_lava'):
                        reward -= 2
                    if prev_action == 'attack 1':
                        h_target = 24
                        if prev_dist <= 4:
                            reward += 0.5
                        else:
                            reward -= 1
                        if 'visible' in data:
                            current_dist = data['visible'][-1]
                            # if block is removed visible would change
                            if (0.1 < (current_dist - prev_dist)):
                                logging.debug('distance is more than before!')
                                reward += 1
                                if prev_block in ('double_plant', 'tallgrass'):
                                    reward -= 0.5
                                tmp = ((30 - h_target) - abs(prev_item[HEIGHT] - h_target))
                                logging.info('tmp dist %f', tmp)
                                tmp = max(tmp, 0) ** 2
                                if h_target < prev_item[HEIGHT]:
                                    tmp /= 3
                                reward += tmp
                                if prev_block not in ('dirt', 'grass', 'stone', 'double_plant', 'tallgrass', 'leaves', 'log'):
                                    reward += 25
                            else:
                                # give small reward for removing block under self
                                prev_height = 30 - self.state_queue[-1]['ypos']
                                curr_height = 30 - data['ypos']
                                if curr_height < prev_height:
                                    tmp = ((30 - h_target) - abs(prev_height - h_target))
                                    logging.debug('removed block!')
                                    logging.debug('tmp dist %f', tmp)
                                    reward += max(tmp, 0) ** 2 / 2
                else:
                    if 'visible' not in data:
                        logging.debug('not visible')
                        reward -= 1
                reward -= 1
                reward += (life - prev_life) * 2
                prev_life = life
                if not mc.is_mission_running():
                    logging.debug('failed in %i steps', t)
                    reward = -100
            logging.debug("current reward %f", reward)
            new_actions = self.agent(data, reward=reward, epsilon=eps)
            eps = max(eps * eps_decay, eps_end)
            logging.debug('epsilon %f', eps)
            data['action'] = self.agent.prev_action
            self.state_queue.append(copy.copy(data))
            if 'visible' in data:
                data.pop('visible')
            self.act(new_actions)
            time.sleep(0.4)
            stop_motion(mc)
            time.sleep(0.1)
            if t == max_t:
                logging.debug("too long")
                stop_motion(mc)
                self.agent.push_final(reward)
                self.mc.sendCommand("quit")
                self.learn(self.agent, self.optimizer)
                break
            total_reward += reward
        # in termial state reward is not added due loop breaking
        total_reward += reward

        aPos = self.mc.getAgentPos()
        if aPos is not None and aPos[1] <= 25:
            solved = True
        logging.debug("Final reward: %f", reward)
        self._end()
        return total_reward, t, solved

    def act(self, actions):
        mc = self.mc
        for act in actions:
            logging.debug('action %s', act)
            if act == 'jump_forward':
                mc.sendCommand('move 0.4')
                mc.sendCommand('jump 1')
            else:
                mc.sendCommand(str(act))

    @classmethod
    def init_mission(cls, i, mc, start_x=None, start_y=None):
        miss = mb.MissionXML()
        video_producer = mb.VideoProducer(width=320 * 4, height=240 * 4, want_depth=cls.want_depth)

        obs = mb.Observations()

        obs = mb.Observations()
        obs.gridNear = [[-1, 1], [-2, 1], [-1, 1]]


        agent_handlers = mb.AgentHandlers(observations=obs,
            all_str=mission_ending)

        agent_handlers = mb.AgentHandlers(observations=obs,
            all_str=mission_ending, video_producer=video_producer)
        # a tree is at -18, 15
        if start_x is None:
            center_x = -18
            center_y = 15

            start_x = center_x + random.choice(numpy.arange(-329, 329))
            start_y = center_y + random.choice(numpy.arange(-329, 329))

        logging.info('starting at ({0}, {1})'.format(start_x, start_y))

        miss = mb.MissionXML(agentSections=[mb.AgentSection(name='Cristina',
                 agenthandlers=agent_handlers,
                                          #    depth
                 agentstart=mb.AgentStart([start_x, 30.0, start_y, 1]))])

        miss.setWorld(mb.flatworld("3;7,25*1,3*3,2;1;stronghold,biome_1,village,decoration,dungeon,lake,mineshaft,lava_lake",
            seed='43',
            forceReset="false"))
        miss.serverSection.initial_conditions.allowedmobs = "Pig Sheep Cow Chicken Ozelot Rabbit Villager"
        # uncomment to disable passage of time:
        miss.serverSection.initial_conditions.time_pass = 'false'
        miss.serverSection.initial_conditions.time_start = "1000"

        if mc is None:
            mc = MCConnector(miss)
        else:
            mc.setMissionXML(miss)
        return mc

