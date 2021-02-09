# 1. predict train data
# 2. predict segments
# 3. visualize
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import tempfile
from mlflow.tracking import MlflowClient
import mlflow
import json
import torch
from src.models.anomalia.datasets import ResmedDatasetEpoch
from src.models.predict import predict_file, predict_smavra
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

RUN_ID = "4d8ddb41e7f340c182a6a62699502d9f"
DEVICE = "cuda"
TRAIN_DATA_PATH = "data/processed/resmed/train/train.pt"

mlflow_client = MlflowClient()

# get processing info
with tempfile.TemporaryDirectory() as tmp_dir:
    with open(mlflow_client.download_artifacts(
        run_id=RUN_ID,
        path="config/preprocessing_config.json",
        dst_path=tmp_dir
    ), "r") as f:
        preprocessing_config = json.load(f)

# load model
smavra = mlflow.pytorch.load_model(
    'runs:/' + RUN_ID + '/model',
    map_location="cuda:0"
)
smavra.eval()

if DEVICE == "cuda":
    smavra.cuda()
else:
    smavra.cpu()

pred_tensor = torch.load(TRAIN_DATA_PATH)

score_dataset = ResmedDatasetEpoch(
    data=pred_tensor,
    batch_size=1,
    device=DEVICE,
    means=torch.Tensor(preprocessing_config["means"]),
    stds=torch.Tensor(preprocessing_config["stds"])
)

col_order = ["mask_press", "resp_flow", "delivered_volum"]

#############################
# get stds
#############################
preds, latents, attention_weights = predict_file(
    model=smavra,
    dataset=score_dataset,
    file_path=TRAIN_DATA_PATH,
    explain_latent=False,
    explain_attention=False,
    seq_len=750,
    column_order=col_order
)

if len(preds) > 1:
    preds = pd.concat(preds, ignore_index=True)
else:
    preds = preds[0]


pred = preds.loc[:, ["epoch_id"] + [c + "_mu" for c in col_order]]

true_vals = pd.DataFrame(
    data=pred_tensor.reshape(-1, 3).numpy(),
    columns=col_order
)

conf_interval = pd.concat([pred, true_vals], axis=1)

conf_interval["mask_pressure_e"] = abs(
    conf_interval["mask_press_mu"] - conf_interval["mask_press"])
conf_interval["resp_flow_e"] = abs(
    conf_interval["resp_flow_mu"] - conf_interval["resp_flow"])
conf_interval["delivered_volum_e"] = abs(
    conf_interval["delivered_volum_mu"] - conf_interval["delivered_volum"])

scale_dict = conf_interval.agg({
    "mask_pressure_e": ["mean", "std"],
    "resp_flow_e": ["mean", "std"],
    "delivered_volum_e": ["mean", "std"]
}).to_dict()

#############################
# get scenarios
#############################

PREDICT_FILE = "data/processed/resmed/score/20201214_120001_0_HRD.edf.parquet"


pf = pq.read_table(PREDICT_FILE).to_pandas()
pf["timestamp"] = pd.to_datetime(pf.timestamp)


#
# Montag, 14.12.:
#
# 16:00 Uhr Maschine ausgeschaltet, dann eingeschaltet als Start der Versuchsreihe
#
# 16:01 Uhr an künstlicher Lunge
#
# 16:06 Uhr an Kanüle, fast verschlossen, Vorne an der Kanüle war nur ein minimaler Lufthauch zu spüren
#
# 16:12 Uhr offenes Ende
#
# 16:17 Uhr Beatmung ausgeschaltet
#

normal = pf.loc[
    (pf.timestamp < "2020-12-14 16:00:00.000000")
].reset_index(drop=True)

artificial_lung = pf.loc[
    (pf.timestamp >= "2020-12-14 16:03:00.000000") &
    (pf.timestamp < "2020-12-14 16:05:00.000000")
].reset_index(drop=True)
closed_end = pf.loc[
    (pf.timestamp >= "2020-12-14 16:10:00.000000")
    & (pf.timestamp < "2020-12-14 16:12:00.000000")
].reset_index(drop=True)
open_end = pf.loc[
    (pf.timestamp >= "2020-12-14 16:14:00.000000")
    & (pf.timestamp < "2020-12-14 16:16:00.000000")
].reset_index(drop=True)

pq.write_table(pa.Table.from_pandas(normal),
               "reports/data/computer_bild/input/normal.parquet")
pq.write_table(pa.Table.from_pandas(artificial_lung),
               "reports/data/computer_bild/input/artificial_lung.parquet")
pq.write_table(pa.Table.from_pandas(closed_end),
               "reports/data/computer_bild/input/closed_end.parquet")
pq.write_table(pa.Table.from_pandas(open_end),
               "reports/data/computer_bild/input/open_end.parquet")


# predict_smavra(
#     run_id=RUN_ID,
#     input_dir="reports/data/computer_bild/input",
#     output_dir="reports/data/computer_bild/output",
#     score_file_pattern="*"
# )
# python src/models/predict.py --run_id=4d8ddb41e7f340c182a6a62699502d9f --input_dir=reports/data/computer_bild/input --output_dir=reports/data/computer_bild/output --score_file_pattern="*"
zmax = 6
zmid = 3
case = "open_end"
df = pq.read_table(
    f"reports/data/computer_bild/output/score/{RUN_ID}/{case}.parquet"
).to_pandas()
#"data/output/score/4d8ddb41e7f340c182a6a62699502d9f/20201214_120001_0_HRD.edf.parquet"#
df = df.loc[:, ["timestamp"] + col_order + [c + "_mu" for c in col_order]]

df["mask_pressure_e"] = abs(
    df["mask_press_mu"] - df["mask_press"]
)
df["resp_flow_e"] = abs(
    df["resp_flow_mu"] - df["resp_flow"]
)
df["delivered_volum_e"] = abs(
    df["delivered_volum_mu"] - df["delivered_volum"]
)

df["mask_pressure_z"] = (
    (df["mask_pressure_e"] - scale_dict["mask_pressure_e"]
     ["mean"]) / scale_dict["mask_pressure_e"]["std"]
).apply(lambda x: x if x <= zmax else zmax)
df["resp_flow_z"] = (
    (df["resp_flow_e"] - scale_dict["resp_flow_e"]
     ["mean"]) / scale_dict["resp_flow_e"]["std"]
).apply(lambda x: x if x <= zmax else zmax)
df["delivered_volum_z"] = (
    (df["delivered_volum_e"] - scale_dict["delivered_volum_e"]
     ["mean"]) / scale_dict["delivered_volum_e"]["std"]
).apply(lambda x: x if x <= zmax else zmax)

# df["mask_pressure_upper"] = df["mask_press_mu"] + \
#     2 * std_dict["mask_pressure_e"]
# df["resp_flow_upper"] = df["resp_flow_mu"] + 2 * std_dict["resp_flow_e"]
# df["delivered_volum_upper"] = df["delivered_volum_mu"] + \
#     2 * std_dict["delivered_volum_e"]

# df["mask_pressure_lower"] = df["mask_press_mu"] - \
#     2 * std_dict["mask_pressure_e"]
# df["resp_flow_lower"] = df["resp_flow_mu"] - 2 * std_dict["resp_flow_e"]
# df["delivered_volum_lower"] = df["delivered_volum_mu"] - \
#     2 * std_dict["delivered_volum_e"]

color_palette: dict = {
    "resp_flow": "rgba(247, 201, 77, 1)",
    "deli_volu": "rgba(64, 145, 182, 1)",
    "mask_press": "rgba(105, 173, 82, 1)",
    "true": "rgba(0, 0, 0, 1)",
    "se_resp_flow": "rgba(247, 201, 77, 1)",
    "se_deli_volu": "rgba(64, 145, 182, 1)",
    "se_mask_pres": "rgba(105, 173, 82, 1)",
}

DEV = False
# subplot -----
fig = make_subplots(
    rows=6,
    cols=1,
    row_heights=[0.27, 0.06, 0.27, 0.06, 0.27, 0.06],
    shared_xaxes=True,
    vertical_spacing=0,
    row_titles=("Flow", "", "Volume", "",
                "Pressure", ""),
    specs=[
        [{"b": 0}],
        [{"b": 0.02}],
        [{"b": 0}],
        [{"b": 0.02}],
        [{"b": 0}],
        [{"b": 0.02}],
    ]
)

# Respiration Flow -----
fig.add_trace(
    go.Scatter(
        x=df.timestamp,
        y=df.resp_flow,
        mode='lines',
        name='Resp Flow',
        line=dict(
            color=color_palette["true"]
        )
    ),
    row=1,
    col=1
)
if DEV:
    fig.add_trace(
        go.Scatter(
            x=df.timestamp,
            y=df.resp_flow_mu,
            mode='lines',
            name='Resp Flow',
            line=dict(
                color=color_palette["resp_flow"]
            )
        ),
        row=1,
        col=1
    )

fig.add_trace(
    go.Heatmap(
        name='',
        x=df.timestamp,
        y=[" " for _ in range(df.shape[0])],
        z=df.resp_flow_z,
        showscale=False,
        zauto=False,
        zmin=0,
        zmid=zmid,
        zmax=zmax
    ),
    row=2,
    col=1
)


# Delivered Volume -----
fig.add_trace(
    go.Scatter(
        x=df.timestamp,
        y=df.delivered_volum,
        mode='lines',
        name='Delivered Volume',
        line=dict(
            color=color_palette["true"]
        )
    ),
    row=3,
    col=1
)
if DEV:
    fig.add_trace(
        go.Scatter(
            x=df.timestamp,
            y=df.delivered_volum_mu,
            mode='lines',
            name='Delivered Volume',
            line=dict(
                color=color_palette["deli_volu"]
            )
        ),
        row=3,
        col=1
    )


fig.add_trace(
    go.Heatmap(
        name='',
        x=df.timestamp,
        y=[" " for _ in range(df.shape[0])],
        z=df.delivered_volum_z,
        showscale=False,
        zmin=0,
        zmid=zmid,
        zmax=zmax
    ),
    row=4,
    col=1
)


# Mask Pressure -----
fig.add_trace(
    go.Scatter(
        x=df.timestamp,
        y=df.mask_press,
        mode='lines',
        name='Mask Pressure',
        line=dict(
            color=color_palette["true"]
        )
    ),
    row=5,
    col=1
)
if DEV:
    fig.add_trace(
        go.Scatter(
            x=df.timestamp,
            y=df.mask_press_mu,
            mode='lines',
            name='Mask Pressure',
            line=dict(
                color=color_palette["mask_press"]
            )
        ),
        row=5,
        col=1
    )

fig.add_trace(
    go.Heatmap(
        name='',
        x=df.timestamp,
        y=[" " for _ in range(df.shape[0])],
        z=df.mask_pressure_z,
        showscale=False,
        zmin=0,
        zmid=zmid,
        zmax=zmax
    ),
    row=6,
    col=1
)


fig.update_layout(
    title_text=f"",
    legend_title=None,
    showlegend=False,
    template="plotly_white",
    # legend=dict(
    #     orientation="h",
    #     yanchor="bottom",
    #     y=-.15,
    #     xanchor="right",
    #     x=1
    # )
    # font=dict(
    # family="Courier New, monospace",
    # size=18
    # )
)


fig.show()
# fig.write_html(f"reports/figures/computer_bild/{case}.html")


fig2 = go.Figure()


hm = go.Heatmap(
    x=df.timestamp,
    y=[" " for _ in range(df.shape[0])],
    z=df.delivered_volum_e,
    showscale=False
)


fig2.add_trace(
    hm

)

fig2.show()
