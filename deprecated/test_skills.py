from utils.log import setup_logger
import torch

import logging
import time


def train_agent(agent, Trainer, save_path, train=True):
    num_repeats = 2200
    eps = 0.36
    eps_start = eps
    eps_end = 0.09
    eps_decay = 1
    optimizer = torch.optim.Adadelta(agent.parameters(), lr=0.001,
                                  weight_decay=0.01)

    optimizer = torch.optim.RMSprop(agent.parameters(),
                                    lr=0.0001,
                                    weight_decay=0.01)

#    optimizer = torch.optim.AdamW(agent.parameters(), lr=0.001,
#                                  weight_decay=0.01)
#

    mc = None
    for i in range(0, num_repeats):
        mc = Trainer.init_mission(i, mc)

        logging.debug("\nMission %d of %d:" % (i + 1, num_repeats))
        mc.safeStart()

        # -- run the agent in the world -- #
        trainer = Trainer(agent, mc, optimizer, eps, i > 15 and train)
        comulative_reward, steps, solved = trainer.run_episode()
        logging.info('episode %i: solved %i: comulative reward: %f', i, solved, comulative_reward)
        logging.debug("eps: %f", eps)
        eps = max(eps * eps_decay, eps_end)

        # -- clean up -- #
        time.sleep(0.5)  # (let the Mod reset)

        if i % 14 == 0:
            torch.save(agent.state_dict(), save_path, _use_new_zipfile_serialization=False)


def train_cliff():
    from cliff import load_agent, Trainer
    path = 'agent_cliff.pth'
    agent = load_agent(path)
    train_agent(agent, Trainer, path, False)

def train_cliff_v1():
    from cliff_v1 import load_agent, Trainer
    path = 'agent_cliff_v1.pth'
    agent = load_agent(path)
    train_agent(agent, Trainer, path, True)

def train_cliff_v2():
    from cliff_v2 import load_agent, Trainer
    path = 'agent_cliff_v2.pth'
    agent = load_agent(path)
    train_agent(agent, Trainer, path, False)


def train_cliff_v3():
    from cliff_v3 import load_agent, Trainer
    path = 'agent_cliff_v3.pth'
    agent = load_agent(path)
    train_agent(agent, Trainer, path, True)


def train_tree():
    from trainable.tree import load_agent, Trainer
    path = 'agent_tree.pth'
    agent = load_agent(path)
    train_agent(agent, Trainer, path, True)

def train_tree_v3():
    from trainable.tree_v3 import load_agent, Trainer
    path = 'agent_tree.pth'
    agent = load_agent(path)
    train_agent(agent, Trainer, path, True)

def train_tree_v1():
    from trainable.tree_v1 import load_agent, Trainer
    path = 'agent_tree_v1.pth'
    agent = load_agent(path)
    train_agent(agent, Trainer, path, False)

def train_tree_v2():
    from trainable.tree_v2 import load_agent, Trainer
    path = 'agent_tree_v2.pth'
    agent = load_agent(path)
    train_agent(agent, Trainer, path, False)

def train_vision():
    from trainable.vision import load_agent, Trainer
    path = 'agent_vision.pth'
    agent = load_agent(path)
    train_agent(agent, Trainer, path, True)

def train_dig_v1():
    from trainable.dig_v1 import load_agent, Trainer
    path = 'agent_dig.pth'
    agent = load_agent(path)
    train_agent(agent, Trainer, path, False)

if __name__ == '__main__':
    setup_logger('train.log')
    #train_tree_v3()
    #train_cliff_v3()
    train_dig_v1()
