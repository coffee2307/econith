"""ECONITH :: training

The A->Z model factory. Think of this package as the "production plant" that
turns raw market ore (Binance ticks) into refined, tradable intelligence:

    collect.py       Phase A  -- mine the raw material (live + historical data)
    label.py         Phase B  -- grade the ore (forward returns + anti-greed reward)
    orchestrator.py  Phase C/D -- run the smelters in parallel (PPO / HMM / world)
    deploy.py        Phase E  -- ship the finished goods to the shop floor (models/)

Every stage reads/writes plain Parquet + YAML so you can inspect the whole
supply chain by hand at any point.
"""
