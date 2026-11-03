"""
The RL bot's game-side code (see ai/rl_bot.py for the bot itself):

    encoding.py    public game state -> the (27, 6) board the network reads
    candidates.py  the 1,024 possible moves, and which are legal right now
    network.py     the trained policy network (one logit per move), run with numpy
    decision.py    board + network -> a bid (pure functions, like mcts/stateless_decision.py)
    weights/       exported trained weights (<name>.npz + <name>.json manifest);
                   active.json names the weights each difficulty plays with

Training happens elsewhere (.RL/, not deployed): .RL/v2/export_v2.py writes
a checkpoint into weights/, and .RL/v2/check_v2_server.py checks this code
against the training code (same boards, same logits, same moves).
"""
