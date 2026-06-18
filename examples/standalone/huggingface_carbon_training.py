"""Example: carbon-aware Hugging Face fine-tuning.

The Trainer pauses at each epoch boundary while the grid is dirty and resumes
when it is clean, so a multi-hour fine-tune runs on clean energy.

    pip install transformers
"""

from transformers import Trainer, TrainingArguments

from integrations.huggingface_carbon import CarbonAwareTrainerCallback

# model, train_dataset, etc. defined elsewhere
args = TrainingArguments(output_dir="./out", num_train_epochs=3)

trainer = Trainer(
    model=...,
    args=args,
    train_dataset=...,
    callbacks=[CarbonAwareTrainerCallback(zones="auto:green", max_carbon=200)],
)
trainer.train()
