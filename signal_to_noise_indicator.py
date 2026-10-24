#!/usr/bin/env python3
"""Signal-to-noise ratio indicator for OHLC price candles.

Definition used here (matching the supplied transcript):
    candle_snr = abs(Close - Open) / (High - Low)

The value is between 0 and 1:
    0.0 = close and open are the same (mostly wick / low directional body)
    1.0 = candle body spans the full high-low range (almost no wick)

The rolling SNR is the simple average of candle_snr over the last N bars.
By default, the script creates synthetic OHLC data. To use real data, pass a
CSV with Date, Open, High, Low, Close columns, e.g.:
    python signal_to_noise_indicator.py --csv prices.csv --window 30

Dependencies: pandas, numpy, matplotlib
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Rectangle


def make_synthetic_ohlc(n: int = 180, seed: int = 7) -> pd.DataFrame:
    """Create deterministic, synthetic OHLC data with trending and choppy regimes."""
    if n < 35:
        raise ValueError("Use at least 35 bars so the rolling window has enough history.")

    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    open_ = np.empty(n)
    high = np.empty(n)
    low = np.empty(n)
    close = np.empty(n)
    close[0] = 100.0

    # Directional regimes have larger bodies and relatively short wicks;
    # choppy regimes tend to have smaller bodies and longer wicks.
    for i in range(n):
        if i < int(n * 0.22):       # directional rise
            drift, body_noise, wick_scale = 0.42, 0.28, 0.32
        elif i < int(n * 0.43):     # choppy range
            drift, body_noise, wick_scale = 0.00, 0.34, 0.95
        elif i < int(n * 0.66):     # directional sell-off
            drift, body_noise, wick_scale = -0.38, 0.30, 0.36
        elif i < int(n * 0.80):     # very choppy / indecisive
            drift, body_noise, wick_scale = 0.00, 0.20, 1.08
        else:                       # directional recovery
            drift, body_noise, wick_scale = 0.33, 0.26, 0.40

        if i == 0:
            open_[i] = close[i] - rng.normal(0.0, 0.12)
        else:
            open_[i] = close[i - 1] + rng.normal(0.0, 0.12)
            close[i] = open_[i] + drift + rng.normal(0.0, body_noise)

        # Positive upper/lower shadows; make sure high/low contain open and close.
        upper_wick = abs(rng.normal(0.10, wick_scale * 0.34))
        lower_wick = abs(rng.normal(0.10, wick_scale * 0.34))
        high[i] = max(open_[i], close[i]) + upper_wick
        low[i] = min(open_[i], close[i]) - lower_wick

    frame = pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close},
        index=dates,
    )
    frame.index.name = "Date"
    return frame


def load_ohlc_csv(path: Path) -> pd.DataFrame:
    """Load OHLC data from CSV, accepting common column-name casing."""
    raw = pd.read_csv(path)
    aliases = {str(col).strip().lower(): col for col in raw.columns}

    date_col = next((aliases[k] for k in ("date", "datetime", "timestamp") if k in aliases), None)
    required = {name: aliases.get(name.lower()) for name in ("Open", "High", "Low", "Close")}
    missing = [name for name, col in required.items() if col is None]
    if date_col is None:
        raise ValueError("CSV needs a Date (or DateTime/Timestamp) column.")
    if missing:
        raise ValueError("CSV is missing required OHLC column(s): " + ", ".join(missing))

    raw[date_col] = pd.to_datetime(raw[date_col], errors="coerce")
    raw = raw.dropna(subset=[date_col]).set_index(date_col).sort_index()
    frame = raw[[required["Open"], required["High"], required["Low"], required["Close"]]].copy()
    frame.columns = ["Open", "High", "Low", "Close"]
    for col in frame.columns:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=["Open", "High", "Low", "Close"])
    if frame.empty:
        raise ValueError("No valid OHLC rows found in CSV.")
    return frame


def add_snr(frame: pd.DataFrame, window: int = 30) -> pd.DataFrame:
    """Add per-candle SNR and its trailing rolling mean."""
    if window < 1:
        raise ValueError("Window must be at least 1.")
    out = frame.copy()
    candle_range = (out["High"] - out["Low"]).clip(lower=0)
    body = (out["Close"] - out["Open"]).abs()

    # A zero-range candle has no measurable body/range ratio; keep it undefined.
    out["SNR"] = (body / candle_range.where(candle_range > 0)).clip(0, 1)
    out[f"SNR_{window}bar"] = out["SNR"].rolling(window=window, min_periods=window).mean()
    out["BodyDirection"] = np.sign(out["Close"] - out["Open"]).astype(int)
    return out


def draw_candles(ax: plt.Axes, frame: pd.DataFrame, width: float = 0.62) -> None:
    """Draw a minimal candlestick chart without requiring a finance plotting package."""
    x = mdates.date2num(frame.index.to_pydatetime())
    for xi, (_, row) in zip(x, frame.iterrows()):
        is_up = row["Close"] >= row["Open"]
        color = "#16856b" if is_up else "#d95656"
        ax.vlines(xi, row["Low"], row["High"], color=color, linewidth=0.9, alpha=0.9)
        bottom = min(row["Open"], row["Close"])
        body_height = abs(row["Close"] - row["Open"])
        if body_height < 1e-6:
            ax.hlines(row["Open"], xi - width / 2, xi + width / 2, color=color, linewidth=1.4)
        else:
            ax.add_patch(
                Rectangle(
                    (xi - width / 2, bottom), width, body_height,
                    facecolor=color, edgecolor=color, linewidth=0.5,
                )
            )
    ax.xaxis_date()
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))


def make_chart(data: pd.DataFrame, window: int, output_path: Path) -> None:
    snr_col = f"SNR_{window}bar"
    fig, (price_ax, snr_ax) = plt.subplots(
        2, 1, figsize=(14, 8), sharex=True,
        gridspec_kw={"height_ratios": [2.2, 1.0]},
    )
    fig.patch.set_facecolor("#fbfcfe")
    for ax in (price_ax, snr_ax):
        ax.set_facecolor("#fbfcfe")
        ax.grid(True, alpha=0.20, linewidth=0.7)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    draw_candles(price_ax, data)
    price_ax.set_title("Synthetic OHLC Price + Signal-to-Noise Ratio", loc="left", fontsize=15, weight="bold", pad=12)
    price_ax.set_ylabel("Price")

    x = mdates.date2num(data.index.to_pydatetime())
    snr_ax.plot(x, data["SNR"], color="#9aa5b1", linewidth=0.9, alpha=0.8, label="Daily candle SNR")
    snr_ax.plot(x, data[snr_col], color="#2563eb", linewidth=2.0, label=f"{window}-bar average")
    snr_ax.axhline(0.5, color="#d97706", linestyle="--", linewidth=1.25, label="0.50 reference")
    snr_ax.fill_between(x, 0.5, data[snr_col].to_numpy(), where=(data[snr_col].to_numpy() >= 0.5), color="#16856b", alpha=0.10, interpolate=True)
    snr_ax.set_ylim(0, 1.02)
    snr_ax.set_ylabel("SNR (0–1)")
    snr_ax.set_xlabel("Date")
    snr_ax.legend(loc="upper left", ncols=3, frameon=False, fontsize=9)

    # Keep date labels readable and reduce overlap.
    price_ax.tick_params(axis="x", labelbottom=False)
    fig.autofmt_xdate()
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=160, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Candle-body / candle-range SNR indicator")
    parser.add_argument("--csv", type=Path, help="Optional CSV containing Date, Open, High, Low, Close")
    parser.add_argument("--window", type=int, default=30, help="Rolling average window in bars (default: 30)")
    parser.add_argument("--bars", type=int, default=180, help="Synthetic bars when --csv is not supplied (default: 180)")
    parser.add_argument("--output", type=Path, default=Path("signal_to_noise_demo.png"), help="Output chart PNG")
    parser.add_argument("--data-output", type=Path, default=Path("signal_to_noise_values.csv"), help="Output CSV including SNR values")
    args = parser.parse_args()

    if args.window < 1:
        parser.error("--window must be at least 1")

    frame = load_ohlc_csv(args.csv) if args.csv else make_synthetic_ohlc(args.bars)
    data = add_snr(frame, window=args.window)
    make_chart(data, window=args.window, output_path=args.output)
    args.data_output.parent.mkdir(parents=True, exist_ok=True)
    data.to_csv(args.data_output, index_label="Date", float_format="%.6f")

    rolling = data[f"SNR_{args.window}bar"].dropna()
    latest = rolling.iloc[-1] if not rolling.empty else float("nan")
    latest_date = rolling.index[-1].date() if not rolling.empty else "not available"
    print(f"Bars: {len(data)}")
    print(f"Latest {args.window}-bar SNR: {latest:.3f} ({latest:.1%}) on {latest_date}")
    print(f"Chart saved to: {args.output.resolve()}")
    print(f"CSV saved to:   {args.data_output.resolve()}")


if __name__ == "__main__":
    main()
