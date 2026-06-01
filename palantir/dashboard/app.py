import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import pandas as pd
from datetime import datetime
import dash
from dash import dcc, html, Input, Output, callback
import logging

from database.models import SessionLocal, Signal, Trade, Price
from config.settings import FOREX_PAIRS, ACCOUNT_BALANCE

logger = logging.getLogger(__name__)

app = dash.Dash(
    __name__,
    title="Palantir Trading System",
    meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}],
)

# ── LAYOUT ───────────────────────────────────────────────────────────────────

app.layout = html.Div(
    style={"backgroundColor": "#0a0e1a", "minHeight": "100vh", "fontFamily": "Arial, sans-serif"},
    children=[

        # Header
        html.Div(
            style={"backgroundColor": "#0d1117", "padding": "20px 30px", "borderBottom": "1px solid #1e2d40"},
            children=[
                html.H1("⚡ PALANTIR", style={"color": "#00d4ff", "margin": 0, "fontSize": "28px"}),
                html.P("AI Forex Trading System", style={"color": "#6b7280", "margin": "4px 0 0 0", "fontSize": "14px"}),
            ]
        ),

        # KPI Cards
        html.Div(
            id="kpi-cards",
            style={"display": "flex", "gap": "16px", "padding": "24px 30px", "flexWrap": "wrap"},
        ),

        # Main content
        html.Div(
            style={"display": "flex", "gap": "20px", "padding": "0 30px 30px"},
            children=[

                # Left: Equity Curve + Market Overview
                html.Div(
                    style={"flex": "2"},
                    children=[
                        dcc.Graph(id="equity-curve", style={"marginBottom": "20px"}),
                        dcc.Graph(id="market-overview"),
                    ]
                ),

                # Right: Signals Feed
                html.Div(
                    style={"flex": "1"},
                    children=[
                        html.H3("📡 Ultimi Segnali", style={"color": "#e2e8f0", "marginTop": 0}),
                        html.Div(id="signals-feed"),
                    ]
                ),
            ]
        ),

        # Auto-refresh ogni 30 secondi
        dcc.Interval(id="interval", interval=30_000, n_intervals=0),
    ]
)


# ── CALLBACKS ────────────────────────────────────────────────────────────────

@app.callback(
    [Output("kpi-cards", "children"),
     Output("equity-curve", "figure"),
     Output("market-overview", "figure"),
     Output("signals-feed", "children")],
    Input("interval", "n_intervals")
)
def update_dashboard(n):
    db = SessionLocal()
    try:
        # KPI Data
        signals = db.query(Signal).order_by(Signal.created_at.desc()).limit(100).all()
        trades = db.query(Trade).all()

        total_signals = len(signals)
        sent_signals = len([s for s in signals if s.status in ("sent", "paper")])
        wins = len([t for t in trades if t.outcome == "win"])
        losses = len([t for t in trades if t.outcome == "loss"])
        win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0
        total_pnl = sum(t.pnl or 0 for t in trades)

        # KPI Cards
        kpis = [
            ("📡 Segnali Totali", str(total_signals), "#00d4ff"),
            ("✅ Inviati", str(sent_signals), "#10b981"),
            ("🎯 Win Rate", f"{win_rate:.1f}%", "#10b981" if win_rate >= 55 else "#ef4444"),
            ("💰 P&L Totale", f"{total_pnl:+.2f}", "#10b981" if total_pnl >= 0 else "#ef4444"),
        ]

        kpi_cards = [
            html.Div(
                style={
                    "backgroundColor": "#0d1117",
                    "border": f"1px solid {color}33",
                    "borderRadius": "12px",
                    "padding": "20px 24px",
                    "minWidth": "160px",
                    "flex": "1",
                },
                children=[
                    html.P(label, style={"color": "#6b7280", "margin": "0 0 8px 0", "fontSize": "13px"}),
                    html.H2(value, style={"color": color, "margin": 0, "fontSize": "28px"}),
                ]
            )
            for label, value, color in kpis
        ]

        # Equity Curve
        equity_fig = go.Figure()
        if trades:
            cumulative = []
            balance = ACCOUNT_BALANCE
            for t in sorted(trades, key=lambda x: x.opened_at or datetime.utcnow()):
                balance += (t.pnl or 0)
                cumulative.append({"date": t.closed_at, "balance": balance})

            if cumulative:
                df_eq = pd.DataFrame(cumulative)
                equity_fig.add_trace(go.Scatter(
                    x=df_eq["date"], y=df_eq["balance"],
                    mode="lines", name="Balance",
                    line={"color": "#00d4ff", "width": 2},
                    fill="tozeroy", fillcolor="rgba(0,212,255,0.05)",
                ))

        equity_fig.add_hline(y=ACCOUNT_BALANCE, line_dash="dash", line_color="#4b5563")
        equity_fig.update_layout(
            title={"text": "📈 Equity Curve", "font": {"color": "#e2e8f0", "size": 16}},
            plot_bgcolor="#0d1117", paper_bgcolor="#0d1117",
            font={"color": "#9ca3af"},
            xaxis={"gridcolor": "#1e2d40"},
            yaxis={"gridcolor": "#1e2d40"},
            margin={"t": 50, "b": 40, "l": 60, "r": 20},
            height=300,
        )

        # Market Overview — score per coppia
        market_scores = []
        for symbol in FOREX_PAIRS:
            last_signal = db.query(Signal).filter(Signal.symbol == symbol).order_by(Signal.created_at.desc()).first()
            score = last_signal.raw_score if last_signal else 0
            direction = last_signal.direction if last_signal else "NEUTRAL"
            market_scores.append({"symbol": symbol, "score": score or 0, "direction": direction or "NEUTRAL"})

        df_market = pd.DataFrame(market_scores)
        colors = ["#10b981" if d == "LONG" else "#ef4444" if d == "SHORT" else "#6b7280"
                  for d in df_market["direction"]]

        market_fig = go.Figure(go.Bar(
            x=df_market["symbol"],
            y=df_market["score"],
            marker_color=colors,
            text=[f"{s:.0f}" for s in df_market["score"]],
            textposition="outside",
        ))
        market_fig.update_layout(
            title={"text": "🌍 Market Overview — Score per Coppia", "font": {"color": "#e2e8f0", "size": 16}},
            plot_bgcolor="#0d1117", paper_bgcolor="#0d1117",
            font={"color": "#9ca3af"},
            xaxis={"gridcolor": "#1e2d40"},
            yaxis={"gridcolor": "#1e2d40", "range": [0, 110]},
            margin={"t": 50, "b": 40, "l": 60, "r": 20},
            height=280,
        )

        # Signals Feed
        recent_signals = db.query(Signal).order_by(Signal.created_at.desc()).limit(10).all()
        signal_cards = []
        for sig in recent_signals:
            status_color = {"sent": "#10b981", "paper": "#00d4ff", "rejected": "#ef4444"}.get(sig.status, "#6b7280")
            dir_emoji = "📈" if sig.direction == "LONG" else "📉" if sig.direction == "SHORT" else "⏸"
            signal_cards.append(
                html.Div(
                    style={
                        "backgroundColor": "#0d1117",
                        "border": f"1px solid {status_color}33",
                        "borderRadius": "8px",
                        "padding": "12px 16px",
                        "marginBottom": "10px",
                    },
                    children=[
                        html.Div(
                            style={"display": "flex", "justifyContent": "space-between"},
                            children=[
                                html.Span(f"{dir_emoji} {sig.symbol}", style={"color": "#e2e8f0", "fontWeight": "bold"}),
                                html.Span(sig.status or "N/A", style={"color": status_color, "fontSize": "12px"}),
                            ]
                        ),
                        html.P(sig.strategy_name or "N/A", style={"color": "#6b7280", "margin": "4px 0 0 0", "fontSize": "12px"}),
                        html.P(f"Score: {sig.raw_score or 0:.0f}", style={"color": "#9ca3af", "margin": "2px 0 0 0", "fontSize": "12px"}),
                    ]
                )
            )

        return kpi_cards, equity_fig, market_fig, signal_cards

    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("🚀 Dashboard avviata su http://localhost:8050")
    app.run(debug=False, host="0.0.0.0", port=8050)
