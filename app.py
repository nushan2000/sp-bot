#!/usr/bin/env python3
"""SP Bot - run:  python3 app.py"""
import competitors, config, db, monitor, server

if __name__ == "__main__":
    db.init()
    cfg = config.load()
    monitor.start()
    competitors.start()
    try:
        server.serve(cfg)
    except KeyboardInterrupt:
        print("bye")
