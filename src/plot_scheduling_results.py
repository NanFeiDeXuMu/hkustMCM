"""Paper figures from saved, independently auditable scheduling outputs."""
from pathlib import Path
import os

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / "tmp/matplotlib"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

COLORS = ["#567086", "#D08C39", "#218B82", "#8C70AD"]
LABELS = {"fcfs":"FCFS", "sjf":"Strict SJF", "backfill":"Backfill 128", "backfill_l512":"Backfill 512"}
Q3LABELS = {"flex25":"p=.25", "flex50":"p=.50 (center)", "flex100":"p=1.00", "speed2":"speed=2",
            "cap10":"cap=.10", "cap50":"cap=.50", "seed18":"seed 18", "seed19":"seed 19"}


def save(fig, directory: Path, name: str):
    directory.mkdir(parents=True, exist_ok=True)
    fig.savefig(directory / f"{name}.png", dpi=190, bbox_inches="tight")
    fig.savefig(directory / f"{name}.svg", bbox_inches="tight")
    plt.close(fig)


def question2():
    directory = ROOT / "outputs/question2"
    comparison = pd.read_csv(directory / "comparison.csv").set_index("scenario").loc[list(LABELS)]
    fig, axes = plt.subplots(2, 2, figsize=(10.5, 7), layout="constrained")
    for ax, field, title, unit, multiplier in [
        (axes[0,0], "wait_hours_mean", "Mean waiting time (all jobs)", "hours", 1),
        (axes[0,1], "wait_hours_p95", "95th-percentile waiting time", "hours", 1),
        (axes[1,0], "completed_at_90d", "Completed by day 90", "thousand jobs", .001),
        (axes[1,1], "gpu_allocation_rate_90d", "GPU allocation during days 0-90", "% of physical capacity", 100),
    ]:
        values = comparison[field].to_numpy() * multiplier
        bars = ax.bar(list(LABELS.values()), values, color=COLORS, width=.65)
        ax.bar_label(bars, fmt="%.1f", padding=3, fontsize=9)
        ax.set_ylim(0, max(values)*1.18)
        ax.set_title(title, loc="left", fontsize=11)
        ax.set_ylabel(unit)
        ax.tick_params(axis="x", labelsize=9, rotation=12)
        ax.grid(axis="y", alpha=.22)
        ax.set_axisbelow(True)
    save(fig, directory/"figures", "01_policy_comparison")

    fig, ax = plt.subplots(figsize=(8.5, 4.8), layout="constrained")
    for color, (name,label) in zip(COLORS, LABELS.items()):
        schedule = pd.read_csv(directory/name/"schedule.csv", usecols=["wait_time"])
        wait = np.sort(schedule.wait_time.to_numpy()/3600)
        selected = np.unique(np.linspace(0,len(wait)-1,3000).astype(int))
        ax.plot(wait[selected], (selected+1)/len(wait)*100, label=label, color=color, lw=1.7)
    ax.set_xscale("symlog", linthresh=.01)
    ax.set_xlabel("Waiting time (hours; linear near zero, logarithmic thereafter)")
    ax.set_ylabel("Jobs with wait at most x (%)")
    ax.set_ylim(0,101)
    ax.set_xlim(left=0)
    ax.grid(alpha=.2)
    ax.legend(loc="lower right")
    ax.set_title("Waiting-time distributions include every submitted job", loc="left")
    save(fig,directory/"figures","02_waiting_cdf")

    fig, axes = plt.subplots(1,2,figsize=(10.5,4.4),layout="constrained")
    groups=["<=1h","1h-1d","1d-7d",">7d"]
    x=np.arange(len(groups))
    for i,(name,label) in enumerate(list(LABELS.items())[:3]):
        table=pd.read_csv(directory/name/"metrics_by_job_group.csv").set_index("group").loc[groups]
        for ax,field,title in zip(axes,["wait_hours_mean","wait_hours_p95"],["Mean wait","95th-percentile wait"]):
            ax.bar(x+(i-1)*.25,table[field]/24,.24,label=label,color=COLORS[i])
            ax.set_xticks(x,groups)
            ax.set_xlabel("Original runtime group")
            ax.set_ylabel("days")
            ax.set_title(title,loc="left")
            ax.grid(axis="y",alpha=.2)
            ax.set_axisbelow(True)
    axes[0].legend(fontsize=9)
    save(fig,directory/"figures","03_waiting_by_duration")


def question3():
    directory=ROOT/"outputs/question3"
    comparison=pd.read_csv(directory/"comparison.csv").set_index("scenario")
    names=list(Q3LABELS)
    baseline=ROOT/"outputs/question2/sjf"
    base=pd.read_csv(baseline/"metrics_by_requested_model.csv").set_index("requested_model")
    records={name:pd.read_csv(directory/name/"metrics_by_requested_model.csv").set_index("requested_model") for name in names}
    fig,axes=plt.subplots(1,2,figsize=(11,4.8),layout="constrained")
    labels=[Q3LABELS[name] for name in names]
    for ax,model in zip(axes,["A10","A100-SXM4-80GB"]):
        values=[records[name].loc[model,"wait_hours_mean"] for name in names]
        ax.barh(labels,values,color=[COLORS[2] if n!="speed2" else COLORS[1] for n in names])
        ax.axvline(base.loc[model,"wait_hours_mean"],color="#333333",ls="--",label="No migration")
        ax.invert_yaxis()
        ax.set_xlabel("Mean wait (hours; original model cohort)")
        ax.set_title(model,loc="left")
        ax.grid(axis="x",alpha=.2)
        ax.set_axisbelow(True)
        ax.legend(fontsize=9)
    save(fig,directory/"figures","01_migration_sensitivity")

    fig,axes=plt.subplots(1,2,figsize=(10.5,4.8),layout="constrained")
    fields=["wait_increased_fraction","max_wait_increase_hours"]
    titles=["Native jobs with increased wait (%)","Largest paired wait increase (hours)"]
    for i,model in enumerate(["A100-SXM4-80GB","A800-SXM4-80GB"]):
        tables=[pd.read_csv(directory/name/"paired_wait_changes.csv").set_index("requested_model") for name in names]
        for ax,field,title in zip(axes,fields,titles):
            values=[table.loc[model,field]*(100 if field==fields[0] else 1) for table in tables]
            ax.barh(np.arange(len(names))+(i-.5)*.34,values,height=.32,label=model,color=COLORS[i])
            ax.set_yticks(np.arange(len(names)),labels)
            ax.invert_yaxis() if i==0 else None
            ax.set_xlabel(title)
            ax.grid(axis="x",alpha=.2)
            ax.set_axisbelow(True)
    axes[0].legend(fontsize=8,loc="upper center",bbox_to_anchor=(.5,1.07),ncol=2)
    save(fig,directory/"figures","02_native_job_cost")

    models=["A10","A100-SXM4-80GB","A800-SXM4-80GB"]
    cases=[("No migration",baseline),("Center: p=.5, s=1, b=.25",directory/"flex50"),("Assumed speedup=2",directory/"speed2")]
    fig,axes=plt.subplots(1,2,figsize=(10.5,4.4),layout="constrained")
    for i,(label,path) in enumerate(cases):
        table=pd.read_csv(path/"metrics_by_assigned_model.csv").set_index("assigned_model").loc[models]
        for ax,resource in zip(axes,["gpu","cpu"]):
            ax.bar(np.arange(3)+(i-1)*.25,table[f"{resource}_allocation_rate_90d"]*100,.24,label=label,color=COLORS[i])
            ax.set_xticks(np.arange(3),["A10","A100","A800"])
            ax.set_ylabel("Allocated capacity during days 0-90 (%)")
            ax.set_ylim(0,105)
            ax.set_title(resource.upper(),loc="left")
            ax.grid(axis="y",alpha=.2)
            ax.set_axisbelow(True)
    axes[0].legend(fontsize=8)
    save(fig,directory/"figures","03_pool_allocation")


if __name__ == "__main__":
    plt.rcParams.update({"font.size":10,"axes.spines.top":False,"axes.spines.right":False})
    question2()
    question3()
