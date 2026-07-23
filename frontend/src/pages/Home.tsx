import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  Activity,
  ArrowRight,
  Bot,
  ClipboardList,
  Loader2,
  RefreshCw,
  ScrollText,
  ShieldCheck,
  TrendingDown,
  TrendingUp,
  Wallet,
} from "lucide-react";
import {
  api,
  type DashboardSummary,
  type DailyPnlPoint,
  type MonthlyPnlPoint,
  type PeriodPnl,
} from "@/lib/api";
import { echarts } from "@/lib/echarts";
import { useDarkMode } from "@/hooks/useDarkMode";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

function formatMoney(value: number, currency = "INR"): string {
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      maximumFractionDigits: 2,
    }).format(value);
  } catch {
    return `${currency} ${value.toFixed(2)}`;
  }
}

function formatPct(value: number): string {
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

function pnlTone(value: number): string {
  if (value > 0) return "text-success";
  if (value < 0) return "text-danger";
  return "text-muted-foreground";
}

function PeriodCard({ period, delay }: { period: PeriodPnl; delay: number }) {
  const positive = period.pnl_pct > 0;
  const negative = period.pnl_pct < 0;
  return (
    <article
      className="dash-rise group relative overflow-hidden rounded-2xl border border-white/10 bg-card/80 p-5 backdrop-blur-sm"
      style={{ animationDelay: `${delay}ms` }}
    >
      <div
        className={cn(
          "pointer-events-none absolute inset-x-0 top-0 h-1 opacity-80 transition group-hover:opacity-100",
          positive && "bg-success",
          negative && "bg-danger",
          !positive && !negative && "bg-primary/60",
        )}
      />
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
            {period.label}
          </p>
          <p className={cn("mt-2 text-3xl font-semibold tracking-tight tabular-nums", pnlTone(period.pnl_pct))}>
            {formatPct(period.pnl_pct)}
          </p>
        </div>
        <span
          className={cn(
            "inline-flex h-9 w-9 items-center justify-center rounded-xl border",
            positive && "border-success/30 bg-success/10 text-success",
            negative && "border-danger/30 bg-danger/10 text-danger",
            !positive && !negative && "border-border bg-muted/40 text-muted-foreground",
          )}
        >
          {positive ? <TrendingUp className="h-4 w-4" /> : negative ? <TrendingDown className="h-4 w-4" /> : <Wallet className="h-4 w-4" />}
        </span>
      </div>
      <dl className="mt-4 grid grid-cols-3 gap-2 text-xs text-muted-foreground">
        <div>
          <dt>Trades</dt>
          <dd className="mt-0.5 font-medium text-foreground">{period.trades}</dd>
        </div>
        <div>
          <dt>Wins</dt>
          <dd className="mt-0.5 font-medium text-success">{period.wins}</dd>
        </div>
        <div>
          <dt>Win rate</dt>
          <dd className="mt-0.5 font-medium text-foreground">{period.win_rate.toFixed(0)}%</dd>
        </div>
      </dl>
    </article>
  );
}

function DailyBars({ days }: { days: DailyPnlPoint[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const { dark } = useDarkMode();

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    chart.setOption({
      backgroundColor: "transparent",
      grid: { left: 40, right: 12, top: 24, bottom: 28 },
      tooltip: { trigger: "axis", valueFormatter: (v: number) => formatPct(Number(v)) },
      xAxis: {
        type: "category",
        data: days.map((d) => d.date.slice(5)),
        axisLabel: { color: "hsl(var(--chart-text))", fontSize: 10 },
        axisLine: { lineStyle: { color: "hsl(var(--chart-axis))" } },
      },
      yAxis: {
        type: "value",
        axisLabel: { color: "hsl(var(--chart-text))", fontSize: 10, formatter: (v: number) => `${v}%` },
        splitLine: { lineStyle: { color: "hsl(var(--chart-grid))" } },
      },
      series: [
        {
          type: "bar",
          data: days.map((d) => ({
            value: d.pnl_pct,
            itemStyle: {
              color: d.pnl_pct >= 0 ? "hsl(var(--success))" : "hsl(var(--danger))",
              borderRadius: [4, 4, 0, 0],
            },
          })),
          animationDuration: 700,
        },
      ],
    });
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.dispose();
    };
  }, [days, dark]);

  return <div ref={ref} style={{ height: 220 }} />;
}

function MonthlyLine({ months }: { months: MonthlyPnlPoint[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const { dark } = useDarkMode();

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    chart.setOption({
      backgroundColor: "transparent",
      grid: { left: 40, right: 12, top: 24, bottom: 28 },
      tooltip: { trigger: "axis", valueFormatter: (v: number) => formatPct(Number(v)) },
      xAxis: {
        type: "category",
        data: months.map((m) => m.month.slice(2)),
        axisLabel: { color: "hsl(var(--chart-text))", fontSize: 10 },
        axisLine: { lineStyle: { color: "hsl(var(--chart-axis))" } },
      },
      yAxis: {
        type: "value",
        axisLabel: { color: "hsl(var(--chart-text))", fontSize: 10, formatter: (v: number) => `${v}%` },
        splitLine: { lineStyle: { color: "hsl(var(--chart-grid))" } },
      },
      series: [
        {
          type: "line",
          smooth: true,
          data: months.map((m) => m.pnl_pct),
          symbol: "circle",
          symbolSize: 6,
          lineStyle: { width: 2.5, color: "hsl(var(--primary))" },
          itemStyle: { color: "hsl(var(--primary))" },
          areaStyle: {
            color: {
              type: "linear",
              x: 0,
              y: 0,
              x2: 0,
              y2: 1,
              colorStops: [
                { offset: 0, color: "hsla(27, 90%, 52%, 0.28)" },
                { offset: 1, color: "hsla(27, 90%, 52%, 0.02)" },
              ],
            },
          },
          animationDuration: 800,
        },
      ],
    });
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.dispose();
    };
  }, [months, dark]);

  return <div ref={ref} style={{ height: 220 }} />;
}

export function Home() {
  const { t } = useTranslation();
  const [data, setData] = useState<DashboardSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [depositAmount, setDepositAmount] = useState("100000");
  const [depositing, setDepositing] = useState(false);

  const load = useCallback(async (mode: "initial" | "refresh" = "refresh") => {
    if (mode === "initial") setLoading(true);
    else setRefreshing(true);
    setError(null);
    try {
      setData(await api.getDashboardSummary());
    } catch (err) {
      setError(err instanceof Error ? err.message : t("home.dashboardUnavailable"));
      setData(null);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [t]);

  useEffect(() => {
    void load("initial");
    const timer = window.setInterval(() => void load("refresh"), 30_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const onDeposit = async () => {
    const amount = Number(depositAmount);
    if (!Number.isFinite(amount) || amount <= 0) {
      toast.error("Enter a positive deposit amount");
      return;
    }
    setDepositing(true);
    try {
      await api.depositPaper({ amount, note: "Dashboard deposit", currency: data?.paper_wallet?.currency || "INR" });
      toast.success(`Added ${amount} to paper wallet`);
      await load("refresh");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Deposit failed");
    } finally {
      setDepositing(false);
    }
  };

  return (
    <div className="relative min-h-screen overflow-hidden">
      <div className="pointer-events-none absolute inset-0 dash-atmosphere" aria-hidden />
      <div className="relative mx-auto flex w-full max-w-6xl flex-col gap-8 p-6 lg:p-8">
        <header className="dash-rise flex flex-col gap-5 border-b border-white/10 pb-6 lg:flex-row lg:items-end lg:justify-between">
          <div className="space-y-3">
            <p className="text-xs font-semibold uppercase tracking-[0.22em] text-primary">
              {t("home.brand")}
            </p>
            <h1 className="max-w-2xl text-4xl font-semibold tracking-tight sm:text-5xl">
              {t("home.dashboardTitle")}
            </h1>
            <p className="max-w-2xl text-sm text-muted-foreground sm:text-base">
              {t("home.dashboardSubtitle")}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => void load("refresh")}
              disabled={refreshing || loading}
              className="inline-flex items-center gap-2 rounded-xl border bg-card/70 px-3 py-2 text-sm text-muted-foreground transition hover:bg-muted hover:text-foreground disabled:opacity-60"
            >
              {refreshing ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
              {t("home.refresh")}
            </button>
            <Link
              to="/agent"
              className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90"
            >
              {t("home.startResearch")} <ArrowRight className="h-4 w-4" />
            </Link>
          </div>
        </header>

        {loading && !data ? (
          <div className="flex min-h-48 items-center justify-center rounded-2xl border bg-card/60 text-sm text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            {t("home.loading")}
          </div>
        ) : error && !data ? (
          <div className="rounded-2xl border border-danger/30 bg-danger/5 p-6 text-sm">
            <p className="font-medium text-foreground">{t("home.dashboardUnavailable")}</p>
            <p className="mt-1 text-muted-foreground">{error}</p>
            <button
              type="button"
              onClick={() => void load("initial")}
              className="mt-4 inline-flex items-center gap-2 rounded-lg border px-3 py-1.5"
            >
              <RefreshCw className="h-3.5 w-3.5" /> {t("home.retry")}
            </button>
          </div>
        ) : data ? (
          <>
            <section className="dash-rise rounded-2xl border bg-card/80 p-5" style={{ animationDelay: "20ms" }}>
              <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
                <div>
                  <div className="mb-1 flex items-center gap-2">
                    <Wallet className="h-4 w-4 text-primary" />
                    <h2 className="text-sm font-semibold tracking-wide">{t("home.paperWallet")}</h2>
                  </div>
                  <p className="max-w-2xl text-xs text-muted-foreground">{t("home.paperWalletHint")}</p>
                </div>
                <div className="flex flex-wrap items-end gap-2">
                  <label className="grid gap-1 text-xs text-muted-foreground">
                    {t("home.addMoney")}
                    <input
                      type="number"
                      min={1}
                      step={1000}
                      value={depositAmount}
                      onChange={(e) => setDepositAmount(e.target.value)}
                      className="w-36 rounded-lg border bg-background px-3 py-2 text-sm text-foreground"
                    />
                  </label>
                  <button
                    type="button"
                    onClick={() => void onDeposit()}
                    disabled={depositing}
                    className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:opacity-60"
                  >
                    {depositing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wallet className="h-4 w-4" />}
                    {t("home.deposit")}
                  </button>
                  <Link to="/settings" className="rounded-xl border px-3 py-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground">
                    {t("home.settings")}
                  </Link>
                </div>
              </div>
              <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                {[
                  { label: t("home.cash"), value: formatMoney(data.paper_wallet?.cash ?? 0, data.paper_wallet?.currency), tone: "" },
                  { label: t("home.equity"), value: formatMoney(data.paper_wallet?.equity ?? 0, data.paper_wallet?.currency), tone: "" },
                  { label: t("home.totalPnl"), value: formatMoney(data.paper_wallet?.total_pnl ?? 0, data.paper_wallet?.currency), tone: pnlTone(data.paper_wallet?.total_pnl ?? 0) },
                  { label: t("home.pnlPct"), value: formatPct(data.paper_wallet?.total_pnl_pct ?? 0), tone: pnlTone(data.paper_wallet?.total_pnl_pct ?? 0) },
                  { label: t("home.paperPositions"), value: String(data.paper_wallet?.open_positions ?? 0), tone: "" },
                ].map((item) => (
                  <div key={item.label} className="rounded-xl border bg-muted/20 px-3 py-3">
                    <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{item.label}</div>
                    <div className={cn("mt-1 text-lg font-semibold tabular-nums", item.tone)}>{item.value}</div>
                  </div>
                ))}
              </div>
            </section>

            <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              <PeriodCard period={data.today} delay={40} />
              <PeriodCard period={data.week} delay={90} />
              <PeriodCard period={data.month} delay={140} />
              <PeriodCard period={data.all_time} delay={190} />
            </section>

            <section className="grid gap-4 lg:grid-cols-2">
              <article className="dash-rise rounded-2xl border bg-card/80 p-5" style={{ animationDelay: "220ms" }}>
                <div className="mb-3 flex items-center justify-between">
                  <h2 className="text-sm font-semibold tracking-wide">{t("home.dailyPnl")}</h2>
                  <span className="text-xs text-muted-foreground">{t("home.last30Days")}</span>
                </div>
                <DailyBars days={data.daily} />
              </article>
              <article className="dash-rise rounded-2xl border bg-card/80 p-5" style={{ animationDelay: "280ms" }}>
                <div className="mb-3 flex items-center justify-between">
                  <h2 className="text-sm font-semibold tracking-wide">{t("home.monthlyPnl")}</h2>
                  <span className="text-xs text-muted-foreground">{t("home.last12Months")}</span>
                </div>
                <MonthlyLine months={data.monthly} />
              </article>
            </section>

            <section className="grid gap-4 lg:grid-cols-3">
              <article className="dash-rise rounded-2xl border bg-card/80 p-5" style={{ animationDelay: "320ms" }}>
                <div className="mb-4 flex items-center gap-2">
                  <ShieldCheck className="h-4 w-4 text-primary" />
                  <h2 className="text-sm font-semibold">{t("home.ordersTitle")}</h2>
                </div>
                <ul className="space-y-3 text-sm">
                  <li className="flex items-center justify-between gap-3">
                    <span className="text-muted-foreground">{t("home.canPlaceOrders")}</span>
                    <span className={data.orders.can_place_orders ? "text-success" : "text-danger"}>
                      {data.orders.can_place_orders ? t("home.yes") : t("home.no")}
                    </span>
                  </li>
                  <li className="flex items-center justify-between gap-3">
                    <span className="text-muted-foreground">{t("home.paper")}</span>
                    <span>{data.orders.paper_supported ? t("home.yes") : t("home.no")}</span>
                  </li>
                  <li className="flex items-center justify-between gap-3">
                    <span className="text-muted-foreground">{t("home.upstox")}</span>
                    <span className={data.orders.upstox_configured ? "text-success" : "text-warning"}>
                      {data.orders.upstox_configured ? t("home.configured") : t("home.notConfigured")}
                    </span>
                  </li>
                  <li className="flex items-center justify-between gap-3">
                    <span className="text-muted-foreground">{t("home.openPositions")}</span>
                    <span className="font-medium tabular-nums">{data.open_positions}</span>
                  </li>
                </ul>
                <p className="mt-4 text-xs leading-relaxed text-muted-foreground">{data.orders.note}</p>
                <div className="mt-4 flex flex-wrap gap-2">
                  <Link to="/agent" className="inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs hover:bg-muted">
                    <Bot className="h-3.5 w-3.5" /> {t("home.askAgent")}
                  </Link>
                  <Link to="/runtime" className="inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs hover:bg-muted">
                    <Activity className="h-3.5 w-3.5" /> {t("home.runtime")}
                  </Link>
                  <Link to="/settings" className="inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs hover:bg-muted">
                    {t("home.settings")}
                  </Link>
                </div>
              </article>

              <article className="dash-rise rounded-2xl border bg-card/80 p-5" style={{ animationDelay: "360ms" }}>
                <div className="mb-4 flex items-center gap-2">
                  <ClipboardList className="h-4 w-4 text-primary" />
                  <h2 className="text-sm font-semibold">{t("home.recentTrades")}</h2>
                </div>
                {data.recent_trades.length === 0 ? (
                  <p className="text-sm text-muted-foreground">{t("home.noTradesYet")}</p>
                ) : (
                  <ul className="space-y-2">
                    {data.recent_trades.slice(0, 6).map((trade) => (
                      <li key={trade.signal_id} className="flex items-center justify-between gap-3 rounded-lg px-1 py-1.5 hover:bg-muted/30">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium">{trade.instrument || trade.signal_id}</p>
                          <p className="text-xs text-muted-foreground">
                            {[trade.side, trade.exit_reason].filter(Boolean).join(" · ") || "—"}
                          </p>
                        </div>
                        <span className={cn("shrink-0 text-sm font-medium tabular-nums", pnlTone(trade.pnl_pct))}>
                          {formatPct(trade.pnl_pct)}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </article>

              <article className="dash-rise rounded-2xl border bg-card/80 p-5" style={{ animationDelay: "400ms" }}>
                <div className="mb-4 flex items-center gap-2">
                  <ScrollText className="h-4 w-4 text-primary" />
                  <h2 className="text-sm font-semibold">{t("home.activityLog")}</h2>
                </div>
                {data.recent_audit.length === 0 ? (
                  <p className="text-sm text-muted-foreground">{t("home.noAuditYet")}</p>
                ) : (
                  <ul className="space-y-2">
                    {data.recent_audit.slice(0, 6).map((entry) => (
                      <li key={entry.audit_id || `${entry.ts}-${entry.kind}`} className="rounded-lg px-1 py-1.5">
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-sm font-medium">{entry.kind || "event"}</span>
                          <span className="text-[11px] text-muted-foreground">{entry.ts?.slice(0, 19)}</span>
                        </div>
                        <p className="truncate text-xs text-muted-foreground">
                          {[entry.server, entry.outcome, entry.intent].filter(Boolean).join(" · ") || "—"}
                        </p>
                      </li>
                    ))}
                  </ul>
                )}
              </article>
            </section>

            <section className="dash-rise rounded-2xl border bg-card/80 p-5" style={{ animationDelay: "440ms" }}>
              <div className="mb-4 flex items-center justify-between gap-3">
                <h2 className="text-sm font-semibold">{t("home.recentRuns")}</h2>
                <Link to="/reports" className="text-xs text-primary hover:underline">{t("home.viewReports")}</Link>
              </div>
              {data.recent_runs.length === 0 ? (
                <p className="text-sm text-muted-foreground">{t("home.noRunsYet")}</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[640px] text-sm">
                    <thead className="text-left text-xs uppercase tracking-wide text-muted-foreground">
                      <tr>
                        <th className="pb-2 font-medium">{t("home.run")}</th>
                        <th className="pb-2 font-medium">{t("home.status")}</th>
                        <th className="pb-2 font-medium">{t("home.return")}</th>
                        <th className="pb-2 font-medium">Sharpe</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.recent_runs.map((run) => {
                        const ret = run.total_return;
                        const display =
                          ret == null ? null : Math.abs(ret) <= 2 ? ret * 100 : ret;
                        return (
                          <tr key={run.run_id} className="border-t border-border/60">
                            <td className="py-2.5">
                              <Link to={`/runs/${run.run_id}`} className="font-medium hover:text-primary">
                                {run.prompt || run.run_id}
                              </Link>
                              <div className="text-xs text-muted-foreground">{run.run_id}</div>
                            </td>
                            <td className="py-2.5 capitalize text-muted-foreground">{run.status}</td>
                            <td className={cn("py-2.5 tabular-nums", display == null ? "" : pnlTone(display))}>
                              {display == null ? "—" : formatPct(display)}
                            </td>
                            <td className="py-2.5 tabular-nums text-muted-foreground">
                              {run.sharpe == null ? "—" : run.sharpe.toFixed(2)}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          </>
        ) : null}
      </div>
    </div>
  );
}
