"""Project and Trial. The trade log is a JSON column: a few hundred rows per trial at most."""

from django.db import models


class Project(models.Model):
    name = models.CharField(max_length=200, default="Untitled strategy")
    instrument = models.CharField(max_length=16)
    timeframe = models.CharField(max_length=8)
    holdout_boundary = models.DateTimeField(null=True, blank=True)  # from the manifest when created
    trial_count = models.IntegerField(default=0)  # the honest counter: new spec hashes only
    exam_used = models.BooleanField(default=False)
    locked_spec_hash = models.CharField(max_length=64, blank=True, default="")  # set by the exam
    is_demo = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.name} ({self.instrument} {self.timeframe})"


class Trial(models.Model):
    DEVELOPMENT, HOLDOUT = "development", "holdout"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="trials")
    number = models.IntegerField()
    stage = models.CharField(max_length=16, default=DEVELOPMENT)
    spec = models.JSONField()  # canonical
    spec_hash = models.CharField(max_length=64, db_index=True)
    summary = models.TextField(blank=True, default="")  # what changed vs the previous trial
    card = models.TextField(blank=True, default="")  # rules card text
    metrics = models.JSONField(default=dict)
    verdict = models.JSONField(default=dict)
    baseline = models.JSONField(default=dict)
    stress = models.JSONField(null=True, blank=True)
    skipped = models.JSONField(default=dict)
    trades = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["number", "id"]
        constraints = [models.UniqueConstraint(fields=["project", "spec_hash", "stage"], name="one_run_per_spec_and_stage")]

    def __str__(self) -> str:
        return f"trial {self.number} of {self.project_id} ({self.stage})"
