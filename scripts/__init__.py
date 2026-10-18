"""
Runnable entry points other than the website (web_server.py, at the repo
root). Run each from the repo root as a module so the `highsociety`
package is importable:

    python3 -m scripts.play_cli                  # local hot-seat CLI game
    python3 -m scripts.network_server --players 2
    python3 -m scripts.network_client --host <ip> --port 8888
    python3 -m scripts.network_spectator_client --host <ip> --port 8889
    python3 -m scripts.bot_evaluator ...         # bot-vs-bot strength comparison
    python3 -m scripts.selfplay_job              # RL self-play data (Render cron job)
    python3 -m scripts.verify_replay ...         # check stored games replay exactly
"""
