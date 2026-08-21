import { useState, useEffect } from 'react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
  AreaChart,
  Area,
  Cell,
} from 'recharts';
import { Separator } from '../ui/separator';
import { api } from '../../api';
import type { RegionScopeStats, StatisticsResponse } from '../../types';

function formatPercent(value: number): string {
  return `${value.toFixed(1)}%`;
}

/**
 * Regional flood-scope adoption. Deliberately shows fractions rather than bare
 * percentages: with adoption this sparse, "3 of 117" communicates the sample
 * size that "2.6%" hides.
 */
function RegionScopeStatsPanel({ stats }: { stats: RegionScopeStats }) {
  // Corrupt RF captures land in the packet table with random headers, some of
  // which claim to be region-scoped. At or below the measured floor there is
  // nothing to report but noise, so withhold the percentage and say so.
  const floor = stats.false_positive_floor;
  const withinNoise = stats.scoped_messages <= floor;
  // Real-world scoping is currently rare enough that the share rounds to "0.0%",
  // which reads as a broken widget. Below that resolution the fraction alone is
  // the honest presentation.
  const showTrafficPct = stats.total_messages > 0 && !withinNoise && stats.scoped_pct >= 0.05;

  return (
    <div>
      <h3 className="text-base font-semibold tracking-tight mb-2">Region Scope (24h)</h3>
      <p className="text-[0.8125rem] text-muted-foreground mb-3">
        How much local traffic uses regional flood scoping. Traffic covers all channel messages
        heard, including channels you have no key for; senders only counts channels you can decrypt,
        so the two use different denominators and will not match.
      </p>
      <div className="space-y-2">
        <div className="flex justify-between items-center gap-4">
          <span className="text-sm text-muted-foreground">Scoped messages</span>
          <span className="font-medium text-right">
            {stats.scoped_messages.toLocaleString()} of {stats.total_messages.toLocaleString()}
            {showTrafficPct && (
              <span className="text-muted-foreground"> ({formatPercent(stats.scoped_pct)})</span>
            )}
          </span>
        </div>
        <div className="flex justify-between items-center gap-4">
          <span className="text-sm text-muted-foreground">Senders using regions</span>
          <span className="font-medium text-right">
            {stats.scoped_senders.toLocaleString()} of {stats.total_senders.toLocaleString()}
            {stats.total_senders > 0 && (
              <span className="text-muted-foreground">
                {' '}
                ({formatPercent(stats.scoped_senders_pct)})
              </span>
            )}
          </span>
        </div>
      </div>
      {floor > 0 && stats.scoped_messages > 0 && (
        <p className="text-[0.8125rem] text-muted-foreground mt-2">
          {withinNoise
            ? `Scoped message count is at or below the estimated false-positive floor (${floor.toFixed(0)}) from corrupt packet captures, so it is not evidence of regional adoption.`
            : `Includes an estimated ${floor.toFixed(0)} false positives from corrupt packet captures.`}{' '}
          The sender count is unaffected — it requires successful decryption.
        </p>
      )}
      {stats.total_messages === 0 && (
        <p className="text-sm text-muted-foreground mt-2">
          No channel messages heard in the last 24 hours.
        </p>
      )}
    </div>
  );
}

const CHANNEL_BAR_COLORS = ['#0ea5e9', '#10b981', '#f59e0b', '#f43f5e', '#8b5cf6'];

const TOOLTIP_STYLE = {
  contentStyle: {
    backgroundColor: 'hsl(var(--popover))',
    border: '1px solid hsl(var(--border))',
    borderRadius: '6px',
    fontSize: '11px',
    color: 'hsl(var(--popover-foreground))',
  },
  itemStyle: { color: 'hsl(var(--popover-foreground))' },
  labelStyle: { color: 'hsl(var(--muted-foreground))' },
} as const;

function formatTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}

function formatDateTime(ts: number): string {
  const d = new Date(ts * 1000);
  return (
    d.toLocaleDateString([], { month: 'short', day: 'numeric' }) +
    ' ' +
    d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })
  );
}

function PacketsPerHourChart({ buckets }: { buckets: { timestamp: number; count: number }[] }) {
  // Fill gaps so hours with zero packets still appear on the chart
  const filled: { timestamp: number; count: number }[] = [];
  if (buckets.length > 0) {
    const first = buckets[0].timestamp;
    const last = buckets[buckets.length - 1].timestamp;
    const byTs = new Map(buckets.map((b) => [b.timestamp, b.count]));
    for (let ts = first; ts <= last; ts += 3600) {
      filled.push({ timestamp: ts, count: byTs.get(ts) ?? 0 });
    }
  }

  const data = filled.map((b, i) => ({
    idx: i,
    label: formatDateTime(b.timestamp),
    count: b.count,
  }));

  // Show ~6 evenly-spaced tick labels
  const tickCount = Math.min(6, data.length);
  const tickIndices: number[] = [];
  if (data.length > 1) {
    for (let i = 0; i < tickCount; i++) {
      tickIndices.push(Math.round((i / (tickCount - 1)) * (data.length - 1)));
    }
  }

  return (
    <ResponsiveContainer width="100%" height={140}>
      <AreaChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: -8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
        <XAxis
          dataKey="idx"
          type="number"
          domain={[0, data.length - 1]}
          tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }}
          tickLine={false}
          axisLine={false}
          ticks={tickIndices}
          tickFormatter={(idx) => data[idx]?.label ?? ''}
        />
        <YAxis
          tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }}
          tickLine={false}
          axisLine={false}
          allowDecimals={false}
        />
        <RechartsTooltip
          {...TOOLTIP_STYLE}
          cursor={{
            stroke: 'hsl(var(--muted-foreground))',
            strokeWidth: 1,
            strokeDasharray: '3 3',
          }}
          labelFormatter={(idx) => data[Number(idx)]?.label ?? ''}
          formatter={(value) => [`${Number(value).toLocaleString()} packets`, 'Count']}
        />
        <Area
          type="monotone"
          dataKey="count"
          stroke="#0ea5e9"
          fill="#0ea5e9"
          fillOpacity={0.15}
          strokeWidth={1.5}
          dot={false}
          activeDot={{ r: 4, fill: '#0ea5e9', strokeWidth: 2, stroke: 'hsl(var(--popover))' }}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

function NoiseFloorChart({
  samples,
}: {
  samples: { timestamp: number; noise_floor_dbm: number }[];
}) {
  const data = samples.map((s, i) => ({
    idx: i,
    time: formatTime(s.timestamp),
    noise_floor: s.noise_floor_dbm,
  }));

  const tickCount = Math.min(6, samples.length);
  const tickIndices: number[] = [];
  if (samples.length > 1) {
    for (let i = 0; i < tickCount; i++) {
      tickIndices.push(Math.round((i / (tickCount - 1)) * (samples.length - 1)));
    }
  }

  return (
    <ResponsiveContainer width="100%" height={120}>
      <AreaChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: -8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
        <XAxis
          dataKey="idx"
          type="number"
          domain={[0, samples.length - 1]}
          tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }}
          tickLine={false}
          axisLine={false}
          ticks={tickIndices}
          tickFormatter={(idx) => data[idx]?.time ?? ''}
        />
        <YAxis
          tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }}
          tickLine={false}
          axisLine={false}
          domain={['dataMin - 5', 'dataMax + 5']}
          tickFormatter={(v) => `${v}`}
        />
        <RechartsTooltip
          {...TOOLTIP_STYLE}
          cursor={{
            stroke: 'hsl(var(--muted-foreground))',
            strokeWidth: 1,
            strokeDasharray: '3 3',
          }}
          labelFormatter={(idx) => data[Number(idx)]?.time ?? ''}
          formatter={(value) => [`${value} dBm`, 'Noise Floor']}
        />
        <Area
          type="linear"
          dataKey="noise_floor"
          stroke="#8b5cf6"
          fill="#8b5cf6"
          fillOpacity={0.15}
          strokeWidth={1.5}
          dot={false}
          activeDot={{ r: 4, fill: '#8b5cf6', strokeWidth: 2, stroke: 'hsl(var(--popover))' }}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function SettingsStatisticsSection({ className }: { className?: string }) {
  const [stats, setStats] = useState<StatisticsResponse | null>(null);
  const [statsLoading, setStatsLoading] = useState(false);
  const [statsError, setStatsError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setStatsLoading(true);
    setStatsError(false);
    api.getStatistics().then(
      (data) => {
        if (!cancelled) {
          setStats(data);
          setStatsLoading(false);
        }
      },
      () => {
        if (!cancelled) {
          setStatsError(true);
          setStatsLoading(false);
        }
      }
    );
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className={className}>
      {statsLoading && !stats ? (
        <div className="py-8 text-center text-muted-foreground">
          Loading statistics... this can take a while if you have a lot of stored packets.
        </div>
      ) : stats ? (
        <div className="space-y-6">
          {/* Network */}
          <div>
            <h3 className="text-base font-semibold tracking-tight mb-2">Network</h3>
            <div className="grid grid-cols-3 gap-3">
              <div className="text-center p-3 bg-muted/50 rounded-md">
                <div className="text-2xl font-bold">{stats.contact_count}</div>
                <div className="text-xs text-muted-foreground">Contacts</div>
              </div>
              <div className="text-center p-3 bg-muted/50 rounded-md">
                <div className="text-2xl font-bold">{stats.repeater_count}</div>
                <div className="text-xs text-muted-foreground">Repeaters</div>
              </div>
              <div className="text-center p-3 bg-muted/50 rounded-md">
                <div className="text-2xl font-bold">{stats.channel_count}</div>
                <div className="text-xs text-muted-foreground">Channels</div>
              </div>
            </div>
          </div>

          <Separator />

          {/* Messages */}
          <div>
            <h3 className="text-base font-semibold tracking-tight mb-2">Messages</h3>
            <div className="grid grid-cols-3 gap-3">
              <div className="text-center p-3 bg-muted/50 rounded-md">
                <div className="text-2xl font-bold">{stats.total_dms}</div>
                <div className="text-xs text-muted-foreground">Direct Messages</div>
              </div>
              <div className="text-center p-3 bg-muted/50 rounded-md">
                <div className="text-2xl font-bold">{stats.total_channel_messages}</div>
                <div className="text-xs text-muted-foreground">Channel Messages</div>
              </div>
              <div className="text-center p-3 bg-muted/50 rounded-md">
                <div className="text-2xl font-bold">{stats.total_outgoing}</div>
                <div className="text-xs text-muted-foreground">Sent (Outgoing)</div>
              </div>
            </div>
          </div>

          <Separator />

          {/* Activity */}
          <div>
            <h3 className="text-base font-semibold tracking-tight mb-2">Activity</h3>
            <table className="w-full text-sm">
              <thead>
                <tr className="text-muted-foreground">
                  <th className="text-left font-normal pb-1"></th>
                  <th className="text-right font-normal pb-1">1h</th>
                  <th className="text-right font-normal pb-1">24h</th>
                  <th className="text-right font-normal pb-1">7d</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td className="py-1">Contacts heard</td>
                  <td className="text-right py-1">{stats.contacts_heard.last_hour}</td>
                  <td className="text-right py-1">{stats.contacts_heard.last_24_hours}</td>
                  <td className="text-right py-1">{stats.contacts_heard.last_week}</td>
                </tr>
                <tr>
                  <td className="py-1">Repeaters heard</td>
                  <td className="text-right py-1">{stats.repeaters_heard.last_hour}</td>
                  <td className="text-right py-1">{stats.repeaters_heard.last_24_hours}</td>
                  <td className="text-right py-1">{stats.repeaters_heard.last_week}</td>
                </tr>
                <tr>
                  <td className="py-1">Known-channels active</td>
                  <td className="text-right py-1">{stats.known_channels_active.last_hour}</td>
                  <td className="text-right py-1">{stats.known_channels_active.last_24_hours}</td>
                  <td className="text-right py-1">{stats.known_channels_active.last_week}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <Separator />

          {/* Packets */}
          <div>
            <h3 className="text-base font-semibold tracking-tight mb-2">Packets</h3>
            <div className="space-y-2">
              <div className="flex justify-between items-center">
                <span className="text-sm text-muted-foreground">Total stored</span>
                <span className="font-medium">{stats.total_packets}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-sm text-success">Decrypted</span>
                <span className="font-medium text-success">{stats.decrypted_packets}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-sm text-warning">Undecrypted</span>
                <span className="font-medium text-warning">{stats.undecrypted_packets}</span>
              </div>
            </div>
          </div>

          {/* Packets per Hour (72h) */}
          {stats.packets_per_hour_72h?.length > 0 && (
            <>
              <Separator />
              <div>
                <h3 className="text-base font-semibold tracking-tight mb-2">
                  Packets per Hour (72h)
                </h3>
                <PacketsPerHourChart buckets={stats.packets_per_hour_72h} />
              </div>
            </>
          )}

          <Separator />

          {/* Path Hash Width */}
          <div>
            <h3 className="text-base font-semibold tracking-tight mb-2">Path Hash Width (24h)</h3>
            <div className="mb-2 text-xs text-muted-foreground">
              Parsed stored raw packets from the last 24 hours:{' '}
              {stats.path_hash_width_24h.total_packets}
            </div>
            {stats.path_hash_width_24h.total_packets > 0 ? (
              <ResponsiveContainer width="100%" height={120}>
                <BarChart
                  data={[
                    {
                      name: '1-byte',
                      count: stats.path_hash_width_24h.single_byte,
                      pct: stats.path_hash_width_24h.single_byte_pct,
                    },
                    {
                      name: '2-byte',
                      count: stats.path_hash_width_24h.double_byte,
                      pct: stats.path_hash_width_24h.double_byte_pct,
                    },
                    {
                      name: '3-byte',
                      count: stats.path_hash_width_24h.triple_byte,
                      pct: stats.path_hash_width_24h.triple_byte_pct,
                    },
                  ]}
                  margin={{ top: 4, right: 4, bottom: 0, left: -16 }}
                >
                  <CartesianGrid
                    strokeDasharray="3 3"
                    stroke="hsl(var(--border))"
                    vertical={false}
                  />
                  <XAxis
                    dataKey="name"
                    tick={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }}
                    tickLine={false}
                    axisLine={false}
                  />
                  <YAxis
                    tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }}
                    tickLine={false}
                    axisLine={false}
                    allowDecimals={false}
                  />
                  <RechartsTooltip
                    {...TOOLTIP_STYLE}
                    cursor={{ fill: 'hsl(var(--muted))', opacity: 0.5 }}
                    // eslint-disable-next-line @typescript-eslint/no-explicit-any
                    formatter={(value: any, _: any, props: any) => [
                      `${Number(value).toLocaleString()} (${formatPercent(props.payload.pct)})`,
                      'Packets',
                    ]}
                  />
                  <Bar dataKey="count" radius={[4, 4, 0, 0]} maxBarSize={40}>
                    <Cell fill="#0ea5e9" />
                    <Cell fill="#10b981" />
                    <Cell fill="#f59e0b" />
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <p className="text-sm text-muted-foreground">No path data in the last 24 hours.</p>
            )}
          </div>

          <Separator />

          {/* Multibyte Rollout (node-level adoption; traffic-level shares are above) */}
          <div>
            <h3 className="text-base font-semibold tracking-tight mb-2">Multibyte Rollout</h3>
            <p className="text-[0.8125rem] text-muted-foreground mb-2">
              Nodes whose known direct route uses multibyte hop identifiers — who has upgraded, as
              opposed to how much traffic has (shown above).
            </p>
            {stats.multibyte_rollout.contacts_with_route > 0 ? (
              <div className="space-y-1.5 text-sm">
                <div>
                  <span className="font-medium">
                    {stats.multibyte_rollout.contacts_multibyte} of{' '}
                    {stats.multibyte_rollout.contacts_with_route}
                  </span>{' '}
                  <span className="text-muted-foreground">
                    nodes with a known route use multibyte hops
                  </span>
                </div>
                <div>
                  <span className="font-medium">
                    {stats.multibyte_rollout.repeaters_multibyte} of{' '}
                    {stats.multibyte_rollout.repeaters_with_route}
                  </span>{' '}
                  <span className="text-muted-foreground">repeaters</span>
                </div>
                <div className="text-xs text-muted-foreground">
                  By hop width: {stats.multibyte_rollout.single_byte} × 1-byte ·{' '}
                  {stats.multibyte_rollout.double_byte} × 2-byte ·{' '}
                  {stats.multibyte_rollout.triple_byte} × 3-byte
                </div>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                No contacts with a known direct-route hop width yet.
              </p>
            )}
          </div>

          <Separator />

          {/* Region Scope */}
          <RegionScopeStatsPanel stats={stats.region_scope_24h} />

          {/* Busiest Channels */}
          {stats.busiest_channels_24h.length > 0 && (
            <>
              <Separator />
              <div>
                <h3 className="text-base font-semibold tracking-tight mb-2">
                  Busiest Channels (24h)
                </h3>
                <ResponsiveContainer
                  width="100%"
                  height={stats.busiest_channels_24h.length * 28 + 8}
                >
                  <BarChart
                    data={stats.busiest_channels_24h.map((ch) => ({
                      name: ch.channel_name,
                      messages: ch.message_count,
                    }))}
                    layout="vertical"
                    margin={{ top: 0, right: 4, bottom: 0, left: 0 }}
                    barCategoryGap="20%"
                  >
                    <XAxis type="number" hide />
                    <YAxis
                      type="category"
                      dataKey="name"
                      tick={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }}
                      tickLine={false}
                      axisLine={false}
                      width={100}
                    />
                    <RechartsTooltip
                      {...TOOLTIP_STYLE}
                      cursor={{ fill: 'hsl(var(--muted))', opacity: 0.5 }}
                      formatter={(value) => [`${Number(value).toLocaleString()} messages`, null]}
                    />
                    <Bar dataKey="messages" radius={[0, 4, 4, 0]} maxBarSize={16}>
                      {stats.busiest_channels_24h.map((_, i) => (
                        <Cell key={i} fill={CHANNEL_BAR_COLORS[i % CHANNEL_BAR_COLORS.length]} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </>
          )}

          {/* Noise Floor */}
          {stats.noise_floor_24h && (
            <>
              <Separator />
              <div>
                <h3 className="text-base font-semibold tracking-tight mb-2">Noise Floor (24h)</h3>
                {stats.noise_floor_24h.latest_noise_floor_dbm != null && (
                  <div className="mb-2 text-xs text-muted-foreground">
                    Latest reading: {stats.noise_floor_24h.latest_noise_floor_dbm} dBm
                    {stats.noise_floor_24h.latest_timestamp != null &&
                      ` at ${new Date(
                        stats.noise_floor_24h.latest_timestamp * 1000
                      ).toLocaleTimeString([], {
                        hour: '2-digit',
                        minute: '2-digit',
                      })}`}
                  </div>
                )}
                {stats.noise_floor_24h.samples.length > 1 ? (
                  <NoiseFloorChart samples={stats.noise_floor_24h.samples} />
                ) : stats.noise_floor_24h.samples.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    No noise floor samples collected yet. Samples are collected every minute and
                    retained until server restart.
                  </p>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    Only one sample so far ({stats.noise_floor_24h.samples[0].noise_floor_dbm} dBm).
                    More data needed for a chart. Samples are collected every minute and retained
                    until server restart.
                  </p>
                )}
              </div>
            </>
          )}
        </div>
      ) : statsError ? (
        <div className="py-8 text-center text-muted-foreground">Failed to load statistics.</div>
      ) : null}
    </div>
  );
}
