"""
The RL bot's game-side code (see ai/rl_bot.py for the bot itself):

    encoding.py    public game state -> the (27, 6) board the network reads
    candidates.py  legal moves, and the board after each one
    network.py     the trained scoring network, run with numpy
    decision.py    board + network -> a bid (pure functions, like mcts/stateless_decision.py)
    weights/       exported trained weights (<name>.npz + <name>.json manifest);
                   active.json names the weights each difficulty plays with

Training happens elsewhere (.RL/, not deployed): .RL/export_weights.py writes
a checkpoint into weights/, and .RL/check_rl_bot.py checks this code against
the training code (same boards, same scores).
"""
