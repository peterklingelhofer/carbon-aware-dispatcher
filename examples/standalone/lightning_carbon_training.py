"""Carbon-aware PyTorch Lightning training.

Pause training at each epoch boundary while the grid is dirty, so a multi-hour
or multi-day run consumes clean energy. Drop the callback into your Trainer:

    pip install carbon-aware-dispatcher lightning
    python lightning_carbon_training.py

Outside Lightning, use the gate directly in any training loop:

    from integrations.lightning_carbon import wait_until_clean
    for epoch in range(epochs):
        wait_until_clean(zones="auto:green", max_carbon=200)
        train_one_epoch()
"""

from integrations.lightning_carbon import CarbonAwareCallback

# import lightning as L
# from my_model import MyLightningModule, my_datamodule


def main():
    callback = CarbonAwareCallback(
        zones="auto:green",  # or "CISO,GB,FR" etc.
        max_carbon=200,  # gCO2eq/kWh threshold for "clean"
        max_wait_s=6 * 3600,  # give up waiting after 6h, train anyway
        poll_s=900,  # recheck every 15 min
    )
    print("Configured carbon-aware training gate:", callback.zones, callback.max_carbon)
    # trainer = L.Trainer(callbacks=[callback], max_epochs=50)
    # trainer.fit(MyLightningModule(), my_datamodule())


if __name__ == "__main__":
    main()
