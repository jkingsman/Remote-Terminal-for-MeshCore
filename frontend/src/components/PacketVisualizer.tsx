import { useEffect, useRef, useState, useCallback } from 'react';
import {
  forceSimulation,
  forceLink,
  forceManyBody,
  forceCenter,
  forceCollide,
  forceX,
  forceY,
  type Simulation,
  type SimulationNodeDatum,
  type SimulationLinkDatum,
} from 'd3-force';
import { MeshCoreDecoder, PayloadType } from '@michaelhart/meshcore-decoder';
import type { Contact, RawPacket, RadioConfig } from '../types';
import { CONTACT_TYPE_REPEATER } from '../utils/contactAvatar';
import { Checkbox } from './ui/checkbox';

// =============================================================================
// TYPES
// =============================================================================

type NodeType = 'self' | 'repeater' | 'client';
type PacketLabel = 'AD' | 'GT' | 'DM' | 'ACK' | 'TR' | 'RQ' | 'RS' | '?';

interface GraphNode extends SimulationNodeDatum {
  id: string;
  name: string | null;
  type: NodeType;
  isAmbiguous: boolean;
  lastActivity: number;
  lastSeen?: number | null;
  ambiguousNames?: string[];
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
  fx?: number | null;
  fy?: number | null;
}

interface GraphLink extends SimulationLinkDatum<GraphNode> {
  source: string | GraphNode;
  target: string | GraphNode;
  lastActivity: number;
}

interface Particle {
  linkKey: string;
  progress: number;
  speed: number;
  color: string;
  label: PacketLabel;
  fromNodeId: string;
  toNodeId: string;
}

interface ObservedPath {
  nodes: string[];
  snr: number | null;
  timestamp: number;
}

interface PendingPacket {
  key: string;
  label: PacketLabel;
  paths: ObservedPath[];
  firstSeen: number;
  expiresAt: number;
}

interface ParsedPacket {
  payloadType: number;
  pathBytes: string[];
  srcHash: string | null;
  dstHash: string | null;
  advertPubkey: string | null;
  groupTextSender: string | null;
  anonRequestPubkey: string | null;
}

// Traffic pattern tracking for smarter repeater disambiguation
interface TrafficObservation {
  source: string; // Node that originated traffic (could be resolved node ID or ambiguous)
  nextHop: string | null; // Next hop after this repeater (null if final hop before self)
  timestamp: number;
}

interface RepeaterTrafficData {
  prefix: string; // The 1-byte hex prefix (e.g., "32")
  observations: TrafficObservation[];
}

// Analysis result for whether to split an ambiguous repeater
interface RepeaterSplitAnalysis {
  shouldSplit: boolean;
  // If shouldSplit, maps nextHop -> the sources that exclusively route through it
  disjointGroups: Map<string, Set<string>> | null;
}

// =============================================================================
// CONSTANTS
// =============================================================================

const COLORS = {
  background: '#0a0a0a',
  link: '#4b5563',
  ambiguous: '#9ca3af',
  particleAD: '#f59e0b', // amber - advertisements
  particleGT: '#06b6d4', // cyan - group text
  particleDM: '#8b5cf6', // purple - direct messages
  particleACK: '#22c55e', // green - acknowledgments
  particleTR: '#f97316', // orange - trace packets
  particleRQ: '#ec4899', // pink - requests
  particleRS: '#14b8a6', // teal - responses
  particleUnknown: '#6b7280', // gray - unknown
} as const;

const PARTICLE_COLOR_MAP: Record<PacketLabel, string> = {
  AD: COLORS.particleAD,
  GT: COLORS.particleGT,
  DM: COLORS.particleDM,
  ACK: COLORS.particleACK,
  TR: COLORS.particleTR,
  RQ: COLORS.particleRQ,
  RS: COLORS.particleRS,
  '?': COLORS.particleUnknown,
};

const PARTICLE_SPEED = 0.008;
const DEFAULT_OBSERVATION_WINDOW_SEC = 15;
const FORTY_EIGHT_HOURS_MS = 48 * 60 * 60 * 1000;

// Traffic pattern analysis thresholds
// Be conservative - once split, we can't unsplit, so require strong evidence
const MIN_OBSERVATIONS_TO_SPLIT = 20; // Need at least this many unique sources per next-hop group
const MAX_TRAFFIC_OBSERVATIONS = 200; // Per ambiguous prefix, to limit memory
const TRAFFIC_OBSERVATION_MAX_AGE_MS = 30 * 60 * 1000; // 30 minutes - old observations are pruned

const LEGEND_ITEMS = [
  { emoji: '🟢', label: 'You', size: 'text-xl' },
  { emoji: '📡', label: 'Repeater', size: 'text-base' },
  { emoji: '👤', label: 'Node', size: 'text-base' },
  { emoji: '❓', label: 'Unknown', size: 'text-base' },
] as const;

const PACKET_LEGEND_ITEMS = [
  { label: 'AD', color: COLORS.particleAD, description: 'Advertisement' },
  { label: 'GT', color: COLORS.particleGT, description: 'Group Text' },
  { label: 'DM', color: COLORS.particleDM, description: 'Direct Message' },
  { label: 'ACK', color: COLORS.particleACK, description: 'Acknowledgment' },
  { label: 'TR', color: COLORS.particleTR, description: 'Trace' },
  { label: 'RQ', color: COLORS.particleRQ, description: 'Request' },
  { label: 'RS', color: COLORS.particleRS, description: 'Response' },
  { label: '?', color: COLORS.particleUnknown, description: 'Other' },
] as const;

// =============================================================================
// UTILITY FUNCTIONS (Data Layer)
// =============================================================================

function simpleHash(str: string): string {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    hash = (hash << 5) - hash + str.charCodeAt(i);
    hash = hash & hash;
  }
  return Math.abs(hash).toString(16).padStart(8, '0');
}

function parsePacket(hexData: string): ParsedPacket | null {
  try {
    const decoded = MeshCoreDecoder.decode(hexData);
    if (!decoded.isValid) return null;

    const result: ParsedPacket = {
      payloadType: decoded.payloadType,
      pathBytes: decoded.path || [],
      srcHash: null,
      dstHash: null,
      advertPubkey: null,
      groupTextSender: null,
      anonRequestPubkey: null,
    };

    if (decoded.payloadType === PayloadType.TextMessage && decoded.payload.decoded) {
      const payload = decoded.payload.decoded as { sourceHash?: string; destinationHash?: string };
      result.srcHash = payload.sourceHash || null;
      result.dstHash = payload.destinationHash || null;
    } else if (decoded.payloadType === PayloadType.Advert && decoded.payload.decoded) {
      result.advertPubkey = (decoded.payload.decoded as { publicKey?: string }).publicKey || null;
    } else if (decoded.payloadType === PayloadType.GroupText && decoded.payload.decoded) {
      const payload = decoded.payload.decoded as { decrypted?: { sender?: string } };
      result.groupTextSender = payload.decrypted?.sender || null;
    } else if (decoded.payloadType === PayloadType.AnonRequest && decoded.payload.decoded) {
      const payload = decoded.payload.decoded as { senderPublicKey?: string };
      result.anonRequestPubkey = payload.senderPublicKey || null;
    }

    return result;
  } catch {
    return null;
  }
}

function getPacketLabel(payloadType: number): PacketLabel {
  switch (payloadType) {
    case PayloadType.Advert:
      return 'AD';
    case PayloadType.GroupText:
      return 'GT';
    case PayloadType.TextMessage:
      return 'DM';
    case PayloadType.Ack:
      return 'ACK';
    case PayloadType.Trace:
      return 'TR';
    case PayloadType.Request:
    case PayloadType.AnonRequest:
      return 'RQ';
    case PayloadType.Response:
      return 'RS';
    default:
      return '?';
  }
}

function generatePacketKey(parsed: ParsedPacket, rawPacket: RawPacket): string {
  const contentHash = simpleHash(rawPacket.data).slice(0, 8);

  if (parsed.payloadType === PayloadType.Advert && parsed.advertPubkey) {
    return `ad:${parsed.advertPubkey.slice(0, 12)}`;
  }
  if (parsed.payloadType === PayloadType.GroupText) {
    const sender = parsed.groupTextSender || rawPacket.decrypted_info?.sender || '?';
    const channel = rawPacket.decrypted_info?.channel_name || '?';
    return `gt:${channel}:${sender}:${contentHash}`;
  }
  if (parsed.payloadType === PayloadType.TextMessage) {
    return `dm:${parsed.srcHash || '?'}:${parsed.dstHash || '?'}:${contentHash}`;
  }
  if (parsed.payloadType === PayloadType.AnonRequest && parsed.anonRequestPubkey) {
    return `rq:${parsed.anonRequestPubkey.slice(0, 12)}:${contentHash}`;
  }
  return `other:${contentHash}`;
}

function getLinkId(link: GraphLink): { sourceId: string; targetId: string } {
  return {
    sourceId: typeof link.source === 'string' ? link.source : link.source.id,
    targetId: typeof link.target === 'string' ? link.target : link.target.id,
  };
}

function findContactByPrefix(prefix: string, contacts: Contact[]): Contact | null {
  const normalized = prefix.toLowerCase();
  const matches = contacts.filter((c) => c.public_key.toLowerCase().startsWith(normalized));
  return matches.length === 1 ? matches[0] : null;
}

function findContactsByPrefix(prefix: string, contacts: Contact[]): Contact[] {
  const normalized = prefix.toLowerCase();
  return contacts.filter((c) => c.public_key.toLowerCase().startsWith(normalized));
}

function findContactByName(name: string, contacts: Contact[]): Contact | null {
  return contacts.find((c) => c.name === name) || null;
}

function getNodeType(contact: Contact | null | undefined): NodeType {
  return contact?.type === CONTACT_TYPE_REPEATER ? 'repeater' : 'client';
}

function dedupeConsecutive<T>(arr: T[]): T[] {
  return arr.filter((item, i) => i === 0 || item !== arr[i - 1]);
}

/**
 * Analyze traffic patterns for an ambiguous repeater prefix to determine if it
 * should be split into multiple nodes.
 *
 * Logic:
 * - Group observations by nextHop
 * - For each nextHop group, collect the set of sources
 * - If any source appears in multiple nextHop groups → same physical node (hub), don't split
 * - If source sets are completely disjoint → likely different physical nodes, split
 *
 * Returns shouldSplit=true only when we have enough evidence of disjoint routing.
 */
function analyzeRepeaterTraffic(data: RepeaterTrafficData): RepeaterSplitAnalysis {
  const now = Date.now();

  // Filter out old observations
  const recentObservations = data.observations.filter(
    (obs) => now - obs.timestamp < TRAFFIC_OBSERVATION_MAX_AGE_MS
  );

  // Group by nextHop (use "self" for null nextHop - final repeater)
  const byNextHop = new Map<string, Set<string>>();
  for (const obs of recentObservations) {
    const hopKey = obs.nextHop ?? 'self';
    if (!byNextHop.has(hopKey)) {
      byNextHop.set(hopKey, new Set());
    }
    byNextHop.get(hopKey)!.add(obs.source);
  }

  // If only one nextHop group, no need to split
  if (byNextHop.size <= 1) {
    return { shouldSplit: false, disjointGroups: null };
  }

  // Check if any source appears in multiple groups (evidence of hub behavior)
  const allSources = new Map<string, string[]>(); // source -> list of nextHops it uses
  for (const [nextHop, sources] of byNextHop) {
    for (const source of sources) {
      if (!allSources.has(source)) {
        allSources.set(source, []);
      }
      allSources.get(source)!.push(nextHop);
    }
  }

  // If any source routes to multiple nextHops, this is a hub - don't split
  for (const [, nextHops] of allSources) {
    if (nextHops.length > 1) {
      return { shouldSplit: false, disjointGroups: null };
    }
  }

  // Check if we have enough observations in each group to be confident
  for (const [, sources] of byNextHop) {
    if (sources.size < MIN_OBSERVATIONS_TO_SPLIT) {
      // Not enough evidence yet - be conservative, don't split
      return { shouldSplit: false, disjointGroups: null };
    }
  }

  // Source sets are disjoint and we have enough data - split!
  return { shouldSplit: true, disjointGroups: byNextHop };
}

/**
 * Record a traffic observation for an ambiguous repeater prefix.
 * Prunes old observations and limits total count.
 */
function recordTrafficObservation(
  trafficData: Map<string, RepeaterTrafficData>,
  prefix: string,
  source: string,
  nextHop: string | null
): void {
  const normalizedPrefix = prefix.toLowerCase();
  const now = Date.now();

  if (!trafficData.has(normalizedPrefix)) {
    trafficData.set(normalizedPrefix, { prefix: normalizedPrefix, observations: [] });
  }

  const data = trafficData.get(normalizedPrefix)!;

  // Add new observation
  data.observations.push({ source, nextHop, timestamp: now });

  // Prune old observations
  data.observations = data.observations.filter(
    (obs) => now - obs.timestamp < TRAFFIC_OBSERVATION_MAX_AGE_MS
  );

  // Limit total count
  if (data.observations.length > MAX_TRAFFIC_OBSERVATIONS) {
    data.observations = data.observations.slice(-MAX_TRAFFIC_OBSERVATIONS);
  }
}

// =============================================================================
// DATA LAYER HOOK
// =============================================================================

interface UseVisualizerDataOptions {
  packets: RawPacket[];
  contacts: Contact[];
  config: RadioConfig | null;
  showAmbiguousPaths: boolean;
  showAmbiguousNodes: boolean;
  splitAmbiguousByTraffic: boolean;
  chargeStrength: number;
  letEmDrift: boolean;
  particleSpeedMultiplier: number;
  observationWindowSec: number;
  dimensions: { width: number; height: number };
}

interface VisualizerData {
  nodes: Map<string, GraphNode>;
  links: Map<string, GraphLink>;
  particles: Particle[];
  simulation: Simulation<GraphNode, GraphLink> | null;
  stats: { processed: number; animated: number; nodes: number; links: number };
  randomizePositions: () => void;
  expandContract: () => void;
  clearAndReset: () => void;
}

function useVisualizerData({
  packets,
  contacts,
  config,
  showAmbiguousPaths,
  showAmbiguousNodes,
  splitAmbiguousByTraffic,
  chargeStrength,
  letEmDrift,
  particleSpeedMultiplier,
  observationWindowSec,
  dimensions,
}: UseVisualizerDataOptions): VisualizerData {
  const nodesRef = useRef<Map<string, GraphNode>>(new Map());
  const linksRef = useRef<Map<string, GraphLink>>(new Map());
  const particlesRef = useRef<Particle[]>([]);
  const simulationRef = useRef<Simulation<GraphNode, GraphLink> | null>(null);
  const processedRef = useRef<Set<number>>(new Set());
  const pendingRef = useRef<Map<string, PendingPacket>>(new Map());
  const timersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());
  const trafficPatternsRef = useRef<Map<string, RepeaterTrafficData>>(new Map());
  const speedMultiplierRef = useRef(particleSpeedMultiplier);
  const observationWindowRef = useRef(observationWindowSec * 1000);
  const [stats, setStats] = useState({ processed: 0, animated: 0, nodes: 0, links: 0 });

  // Keep refs in sync with props
  useEffect(() => {
    speedMultiplierRef.current = particleSpeedMultiplier;
  }, [particleSpeedMultiplier]);

  useEffect(() => {
    observationWindowRef.current = observationWindowSec * 1000;
  }, [observationWindowSec]);

  // Initialize simulation
  useEffect(() => {
    const sim = forceSimulation<GraphNode, GraphLink>([])
      .force(
        'link',
        forceLink<GraphNode, GraphLink>([])
          .id((d) => d.id)
          .distance(80)
          .strength(0.3)
      )
      .force(
        'charge',
        forceManyBody<GraphNode>()
          .strength((d) => (d.id === 'self' ? -1200 : -200))
          .distanceMax(500)
      )
      .force('center', forceCenter(dimensions.width / 2, dimensions.height / 2))
      .force('collide', forceCollide(40))
      .force(
        'selfX',
        forceX<GraphNode>(dimensions.width / 2).strength((d) => (d.id === 'self' ? 0.1 : 0))
      )
      .force(
        'selfY',
        forceY<GraphNode>(dimensions.height / 2).strength((d) => (d.id === 'self' ? 0.1 : 0))
      )
      .alphaDecay(0.02)
      .velocityDecay(0.5)
      .alphaTarget(0.03);

    simulationRef.current = sim;
    return () => {
      sim.stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- one-time init; dimensions/charge handled by the effect below
  }, []);

  // Update simulation forces when dimensions/charge change
  useEffect(() => {
    const sim = simulationRef.current;
    if (!sim) return;

    sim.force('center', forceCenter(dimensions.width / 2, dimensions.height / 2));
    sim.force(
      'selfX',
      forceX<GraphNode>(dimensions.width / 2).strength((d) => (d.id === 'self' ? 0.1 : 0))
    );
    sim.force(
      'selfY',
      forceY<GraphNode>(dimensions.height / 2).strength((d) => (d.id === 'self' ? 0.1 : 0))
    );
    sim.force(
      'charge',
      forceManyBody<GraphNode>()
        .strength((d) => (d.id === 'self' ? chargeStrength * 6 : chargeStrength))
        .distanceMax(500)
    );
    sim.alpha(0.3).restart();
  }, [dimensions, chargeStrength]);

  // Update alphaTarget when drift preference changes
  useEffect(() => {
    const sim = simulationRef.current;
    if (!sim) return;
    sim.alphaTarget(letEmDrift ? 0.05 : 0);
  }, [letEmDrift]);

  // Ensure self node exists
  useEffect(() => {
    if (!nodesRef.current.has('self')) {
      nodesRef.current.set('self', {
        id: 'self',
        name: config?.name || 'Me',
        type: 'self',
        isAmbiguous: false,
        lastActivity: Date.now(),
        x: dimensions.width / 2,
        y: dimensions.height / 2,
      });
      syncSimulation();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- syncSimulation is stable (no deps), defined below
  }, [config, dimensions]);

  // Reset on option changes
  useEffect(() => {
    processedRef.current.clear();
    const selfNode = nodesRef.current.get('self');
    nodesRef.current.clear();
    if (selfNode) nodesRef.current.set('self', selfNode);
    linksRef.current.clear();
    particlesRef.current = [];
    pendingRef.current.clear();
    timersRef.current.forEach((t) => clearTimeout(t));
    timersRef.current.clear();
    trafficPatternsRef.current.clear();
    setStats({ processed: 0, animated: 0, nodes: selfNode ? 1 : 0, links: 0 });
  }, [showAmbiguousPaths, showAmbiguousNodes, splitAmbiguousByTraffic]);

  const syncSimulation = useCallback(() => {
    const sim = simulationRef.current;
    if (!sim) return;

    const nodes = Array.from(nodesRef.current.values());
    const links = Array.from(linksRef.current.values());

    sim.nodes(nodes);
    const linkForce = sim.force('link') as ReturnType<typeof forceLink<GraphNode, GraphLink>>;
    linkForce?.links(links);

    sim.alpha(0.15).restart();

    setStats((prev) => ({ ...prev, nodes: nodes.length, links: links.length }));
  }, []);

  const addNode = useCallback(
    (
      id: string,
      name: string | null,
      type: NodeType,
      isAmbiguous: boolean,
      ambiguousNames?: string[],
      lastSeen?: number | null
    ) => {
      const existing = nodesRef.current.get(id);
      if (existing) {
        existing.lastActivity = Date.now();
        if (name && !existing.name) existing.name = name;
        if (ambiguousNames) existing.ambiguousNames = ambiguousNames;
        if (lastSeen !== undefined) existing.lastSeen = lastSeen;
      } else {
        const selfNode = nodesRef.current.get('self');
        nodesRef.current.set(id, {
          id,
          name,
          type,
          isAmbiguous,
          lastActivity: Date.now(),
          lastSeen,
          ambiguousNames,
          x: (selfNode?.x ?? 400) + (Math.random() - 0.5) * 100,
          y: (selfNode?.y ?? 300) + (Math.random() - 0.5) * 100,
        });
      }
    },
    []
  );

  const addLink = useCallback((sourceId: string, targetId: string) => {
    const key = [sourceId, targetId].sort().join('->');
    const existing = linksRef.current.get(key);
    if (existing) {
      existing.lastActivity = Date.now();
    } else {
      linksRef.current.set(key, { source: sourceId, target: targetId, lastActivity: Date.now() });
    }
  }, []);

  const publishPacket = useCallback((packetKey: string) => {
    const pending = pendingRef.current.get(packetKey);
    if (!pending) return;

    pendingRef.current.delete(packetKey);
    timersRef.current.delete(packetKey);

    for (const path of pending.paths) {
      const dedupedPath = dedupeConsecutive(path.nodes);
      if (dedupedPath.length < 2) continue;

      for (let i = 0; i < dedupedPath.length - 1; i++) {
        particlesRef.current.push({
          linkKey: [dedupedPath[i], dedupedPath[i + 1]].sort().join('->'),
          progress: -i,
          speed: PARTICLE_SPEED * speedMultiplierRef.current,
          color: PARTICLE_COLOR_MAP[pending.label],
          label: pending.label,
          fromNodeId: dedupedPath[i],
          toNodeId: dedupedPath[i + 1],
        });
      }
    }
  }, []);

  // Resolve a node from various sources and add to graph
  // trafficContext is used when splitAmbiguousByTraffic is enabled to create
  // separate nodes for ambiguous repeaters based on their position in traffic flow
  // myPrefix is the user's own 12-char pubkey prefix - if a node matches, return 'self'
  // trafficContext.packetSource is the original source of the packet (for traffic analysis)
  // trafficContext.nextPrefix is the next hop after this repeater
  const resolveNode = useCallback(
    (
      source: { type: 'prefix' | 'pubkey' | 'name'; value: string },
      isRepeater: boolean,
      showAmbiguous: boolean,
      myPrefix: string | null,
      trafficContext?: { packetSource: string | null; nextPrefix: string | null }
    ): string | null => {
      if (source.type === 'pubkey') {
        if (source.value.length < 12) return null;
        const nodeId = source.value.slice(0, 12).toLowerCase();
        // Check if this is our own identity - return 'self' instead of creating duplicate node
        if (myPrefix && nodeId === myPrefix) {
          return 'self';
        }
        const contact = contacts.find((c) => c.public_key.toLowerCase().startsWith(nodeId));
        addNode(
          nodeId,
          contact?.name || null,
          getNodeType(contact),
          false,
          undefined,
          contact?.last_seen
        );
        return nodeId;
      }

      if (source.type === 'name') {
        const contact = findContactByName(source.value, contacts);
        if (contact) {
          const nodeId = contact.public_key.slice(0, 12).toLowerCase();
          // Check if this is our own identity
          if (myPrefix && nodeId === myPrefix) {
            return 'self';
          }
          addNode(nodeId, contact.name, getNodeType(contact), false, undefined, contact.last_seen);
          return nodeId;
        }
        const nodeId = `name:${source.value}`;
        addNode(nodeId, source.value, 'client', false);
        return nodeId;
      }

      // type === 'prefix'
      const contact = findContactByPrefix(source.value, contacts);
      if (contact) {
        const nodeId = contact.public_key.slice(0, 12).toLowerCase();
        // Check if this is our own identity
        if (myPrefix && nodeId === myPrefix) {
          return 'self';
        }
        addNode(nodeId, contact.name, getNodeType(contact), false, undefined, contact.last_seen);
        return nodeId;
      }

      if (showAmbiguous) {
        const matches = findContactsByPrefix(source.value, contacts);
        const filtered = isRepeater
          ? matches.filter((c) => c.type === CONTACT_TYPE_REPEATER)
          : matches.filter((c) => c.type !== CONTACT_TYPE_REPEATER);

        // If exactly one match after filtering, use it directly (not ambiguous)
        if (filtered.length === 1) {
          const contact = filtered[0];
          const nodeId = contact.public_key.slice(0, 12).toLowerCase();
          addNode(nodeId, contact.name, getNodeType(contact), false, undefined, contact.last_seen);
          return nodeId;
        }

        // Multiple matches or no matches - create ambiguous node
        // When splitAmbiguousByTraffic is enabled for repeaters, use traffic pattern analysis
        if (filtered.length > 1 || (filtered.length === 0 && isRepeater)) {
          const names = filtered.map((c) => c.name || c.public_key.slice(0, 8));
          const lastSeen = filtered.reduce(
            (max, c) => (c.last_seen && (!max || c.last_seen > max) ? c.last_seen : max),
            null as number | null
          );

          // Default: simple ambiguous node ID
          let nodeId = `?${source.value.toLowerCase()}`;
          let displayName = source.value.toUpperCase();

          // When splitAmbiguousByTraffic is enabled, use traffic pattern analysis
          if (splitAmbiguousByTraffic && isRepeater && trafficContext) {
            const prefix = source.value.toLowerCase();

            // Record observation for traffic analysis (only if we have a packet source)
            if (trafficContext.packetSource) {
              recordTrafficObservation(
                trafficPatternsRef.current,
                prefix,
                trafficContext.packetSource,
                trafficContext.nextPrefix
              );
            }

            // Analyze traffic patterns to decide if we should split
            const trafficData = trafficPatternsRef.current.get(prefix);
            if (trafficData) {
              const analysis = analyzeRepeaterTraffic(trafficData);

              if (analysis.shouldSplit && trafficContext.nextPrefix) {
                // Strong evidence of disjoint routing - split by next hop
                const nextShort = trafficContext.nextPrefix.slice(0, 2).toLowerCase();
                nodeId = `?${prefix}:>${nextShort}`;
                displayName = `${source.value.toUpperCase()}:>${nextShort}`;
              }
              // If analysis says don't split, or this is the final repeater (nextPrefix=null),
              // keep the simple ?XX ID
            }
          }

          addNode(
            nodeId,
            displayName,
            isRepeater ? 'repeater' : 'client',
            true,
            names.length > 0 ? names : undefined,
            lastSeen
          );
          return nodeId;
        }
      }

      return null;
    },
    [contacts, addNode, splitAmbiguousByTraffic]
  );

  // Build path from parsed packet
  const buildPath = useCallback(
    (parsed: ParsedPacket, packet: RawPacket, myPrefix: string | null): string[] => {
      const path: string[] = [];
      let packetSource: string | null = null;

      // Add source - and track it for traffic pattern analysis
      if (parsed.payloadType === PayloadType.Advert && parsed.advertPubkey) {
        const nodeId = resolveNode(
          { type: 'pubkey', value: parsed.advertPubkey },
          false,
          false,
          myPrefix
        );
        if (nodeId) {
          path.push(nodeId);
          packetSource = nodeId;
        }
      } else if (parsed.payloadType === PayloadType.AnonRequest && parsed.anonRequestPubkey) {
        // AnonRequest packets contain the full sender public key
        const nodeId = resolveNode(
          { type: 'pubkey', value: parsed.anonRequestPubkey },
          false,
          false,
          myPrefix
        );
        if (nodeId) {
          path.push(nodeId);
          packetSource = nodeId;
        }
      } else if (parsed.payloadType === PayloadType.TextMessage && parsed.srcHash) {
        if (myPrefix && parsed.srcHash.toLowerCase() === myPrefix) {
          path.push('self');
          packetSource = 'self';
        } else {
          const nodeId = resolveNode(
            { type: 'prefix', value: parsed.srcHash },
            false,
            showAmbiguousNodes,
            myPrefix
          );
          if (nodeId) {
            path.push(nodeId);
            packetSource = nodeId;
          }
        }
      } else if (parsed.payloadType === PayloadType.GroupText) {
        const senderName = parsed.groupTextSender || packet.decrypted_info?.sender;
        if (senderName) {
          const nodeId = resolveNode({ type: 'name', value: senderName }, false, false, myPrefix);
          if (nodeId) {
            path.push(nodeId);
            packetSource = nodeId;
          }
        }
      }

      // Add path bytes (repeaters)
      // Pass packetSource for traffic pattern analysis (used to track which sources route through which repeaters)
      for (let i = 0; i < parsed.pathBytes.length; i++) {
        const hexPrefix = parsed.pathBytes[i];
        const nextPrefix = parsed.pathBytes[i + 1] || null;

        const nodeId = resolveNode(
          { type: 'prefix', value: hexPrefix },
          true,
          showAmbiguousPaths,
          myPrefix,
          {
            packetSource,
            nextPrefix,
          }
        );
        if (nodeId) path.push(nodeId);
      }

      // Add destination
      if (parsed.payloadType === PayloadType.TextMessage && parsed.dstHash) {
        if (myPrefix && parsed.dstHash.toLowerCase() === myPrefix) {
          path.push('self');
        } else {
          const nodeId = resolveNode(
            { type: 'prefix', value: parsed.dstHash },
            false,
            showAmbiguousNodes,
            myPrefix
          );
          if (nodeId) path.push(nodeId);
          else path.push('self');
        }
      } else if (path.length > 0) {
        path.push('self');
      }

      // Ensure ends with self
      if (path.length > 0 && path[path.length - 1] !== 'self') {
        path.push('self');
      }

      return dedupeConsecutive(path);
    },
    [resolveNode, showAmbiguousPaths, showAmbiguousNodes]
  );

  // Process packets
  useEffect(() => {
    let newProcessed = 0;
    let newAnimated = 0;
    let needsUpdate = false;
    const myPrefix = config?.public_key?.slice(0, 12).toLowerCase() || null;

    for (const packet of packets) {
      if (processedRef.current.has(packet.id)) continue;
      processedRef.current.add(packet.id);
      newProcessed++;

      // Limit processed set size
      if (processedRef.current.size > 1000) {
        processedRef.current = new Set(Array.from(processedRef.current).slice(-500));
      }

      const parsed = parsePacket(packet.data);
      if (!parsed) continue;

      const path = buildPath(parsed, packet, myPrefix);
      if (path.length < 2) continue;

      // Create links
      for (let i = 0; i < path.length - 1; i++) {
        if (path[i] !== path[i + 1]) {
          addLink(path[i], path[i + 1]);
          needsUpdate = true;
        }
      }

      // Queue for animation
      const packetKey = generatePacketKey(parsed, packet);
      const now = Date.now();
      const existing = pendingRef.current.get(packetKey);

      if (existing && now < existing.expiresAt) {
        existing.paths.push({ nodes: path, snr: packet.snr ?? null, timestamp: now });
      } else {
        if (timersRef.current.has(packetKey)) {
          clearTimeout(timersRef.current.get(packetKey));
        }
        const windowMs = observationWindowRef.current;
        pendingRef.current.set(packetKey, {
          key: packetKey,
          label: getPacketLabel(parsed.payloadType),
          paths: [{ nodes: path, snr: packet.snr ?? null, timestamp: now }],
          firstSeen: now,
          expiresAt: now + windowMs,
        });
        timersRef.current.set(
          packetKey,
          setTimeout(() => publishPacket(packetKey), windowMs)
        );
      }

      // Limit pending size
      if (pendingRef.current.size > 100) {
        const entries = Array.from(pendingRef.current.entries())
          .sort((a, b) => a[1].firstSeen - b[1].firstSeen)
          .slice(0, 50);
        for (const [key] of entries) {
          clearTimeout(timersRef.current.get(key));
          timersRef.current.delete(key);
          pendingRef.current.delete(key);
        }
      }

      newAnimated++;
    }

    if (needsUpdate) syncSimulation();
    if (newProcessed > 0) {
      setStats((prev) => ({
        ...prev,
        processed: prev.processed + newProcessed,
        animated: prev.animated + newAnimated,
      }));
    }
  }, [packets, config, buildPath, addLink, syncSimulation, publishPacket]);

  // Randomize all node positions (except self) and reheat simulation
  const randomizePositions = useCallback(() => {
    const sim = simulationRef.current;
    if (!sim) return;

    const centerX = dimensions.width / 2;
    const centerY = dimensions.height / 2;
    const radius = Math.min(dimensions.width, dimensions.height) * 0.4;

    for (const node of nodesRef.current.values()) {
      if (node.id === 'self') {
        // Keep self at center
        node.x = centerX;
        node.y = centerY;
      } else {
        // Randomize position in a circle around center
        const angle = Math.random() * 2 * Math.PI;
        const r = Math.random() * radius;
        node.x = centerX + r * Math.cos(angle);
        node.y = centerY + r * Math.sin(angle);
      }
      // Clear velocities
      node.vx = 0;
      node.vy = 0;
    }

    // Reheat simulation strongly
    sim.alpha(1).restart();
  }, [dimensions]);

  // Expand to high repulsion, hold, then contract back
  // Also weakens link force during expansion so nodes can actually separate
  const expandContract = useCallback(() => {
    const sim = simulationRef.current;
    if (!sim) return;

    const startChargeStrength = chargeStrength;
    const peakChargeStrength = -5000;
    const startLinkStrength = 0.3;
    const minLinkStrength = 0.02; // Nearly disable links during expansion
    const expandDuration = 1000;
    const holdDuration = 2000;
    const contractDuration = 1000;
    const startTime = performance.now();

    const animate = (now: number) => {
      const elapsed = now - startTime;
      let currentChargeStrength: number;
      let currentLinkStrength: number;

      if (elapsed < expandDuration) {
        // Expanding: ramp up repulsion, weaken links
        const t = elapsed / expandDuration;
        currentChargeStrength =
          startChargeStrength + (peakChargeStrength - startChargeStrength) * t;
        currentLinkStrength = startLinkStrength + (minLinkStrength - startLinkStrength) * t;
      } else if (elapsed < expandDuration + holdDuration) {
        // Hold: stay at peak repulsion, links weak
        currentChargeStrength = peakChargeStrength;
        currentLinkStrength = minLinkStrength;
      } else if (elapsed < expandDuration + holdDuration + contractDuration) {
        // Contracting: restore both forces
        const t = (elapsed - expandDuration - holdDuration) / contractDuration;
        currentChargeStrength = peakChargeStrength + (startChargeStrength - peakChargeStrength) * t;
        currentLinkStrength = minLinkStrength + (startLinkStrength - minLinkStrength) * t;
      } else {
        // Done - restore originals
        sim.force(
          'charge',
          forceManyBody<GraphNode>()
            .strength((d) => (d.id === 'self' ? startChargeStrength * 6 : startChargeStrength))
            .distanceMax(500)
        );
        sim.force(
          'link',
          forceLink<GraphNode, GraphLink>(Array.from(linksRef.current.values()))
            .id((d) => d.id)
            .distance(80)
            .strength(startLinkStrength)
        );
        sim.alpha(0.3).restart();
        return;
      }

      // Apply current strengths
      sim.force(
        'charge',
        forceManyBody<GraphNode>()
          .strength((d) => (d.id === 'self' ? currentChargeStrength * 6 : currentChargeStrength))
          .distanceMax(500)
      );
      sim.force(
        'link',
        forceLink<GraphNode, GraphLink>(Array.from(linksRef.current.values()))
          .id((d) => d.id)
          .distance(80)
          .strength(currentLinkStrength)
      );
      sim.alpha(0.5).restart();

      requestAnimationFrame(animate);
    };

    requestAnimationFrame(animate);
  }, [chargeStrength]);

  // Clear all state and reset to initial (keeps self node only)
  const clearAndReset = useCallback(() => {
    // Clear all pending timers
    for (const timer of timersRef.current.values()) {
      clearTimeout(timer);
    }
    timersRef.current.clear();

    // Clear pending packets
    pendingRef.current.clear();

    // Clear processed packet IDs so they can be re-processed if needed
    processedRef.current.clear();

    // Clear traffic patterns
    trafficPatternsRef.current.clear();

    // Clear particles
    particlesRef.current.length = 0;

    // Clear links
    linksRef.current.clear();

    // Clear nodes except self, then reset self position
    const selfNode = nodesRef.current.get('self');
    nodesRef.current.clear();
    if (selfNode) {
      selfNode.x = dimensions.width / 2;
      selfNode.y = dimensions.height / 2;
      selfNode.vx = 0;
      selfNode.vy = 0;
      selfNode.lastActivity = Date.now();
      nodesRef.current.set('self', selfNode);
    }

    // Reset simulation with just self node
    const sim = simulationRef.current;
    if (sim) {
      sim.nodes(Array.from(nodesRef.current.values()));
      sim.force(
        'link',
        forceLink<GraphNode, GraphLink>([])
          .id((d) => d.id)
          .distance(80)
          .strength(0.3)
      );
      sim.alpha(0.3).restart();
    }

    // Reset stats
    setStats({ processed: 0, animated: 0, nodes: 1, links: 0 });
  }, [dimensions]);

  return {
    nodes: nodesRef.current,
    links: linksRef.current,
    particles: particlesRef.current,
    simulation: simulationRef.current,
    stats,
    randomizePositions,
    expandContract,
    clearAndReset,
  };
}

// =============================================================================
// RENDERING FUNCTIONS
// =============================================================================

function renderLinks(
  ctx: CanvasRenderingContext2D,
  links: GraphLink[],
  nodes: Map<string, GraphNode>
) {
  ctx.strokeStyle = COLORS.link;
  ctx.lineWidth = 2;

  for (const link of links) {
    const { sourceId, targetId } = getLinkId(link);
    const source = nodes.get(sourceId);
    const target = nodes.get(targetId);

    if (source?.x != null && source?.y != null && target?.x != null && target?.y != null) {
      ctx.beginPath();
      ctx.moveTo(source.x, source.y);
      ctx.lineTo(target.x, target.y);
      ctx.stroke();
    }
  }
}

function renderParticles(
  ctx: CanvasRenderingContext2D,
  particles: Particle[],
  nodes: Map<string, GraphNode>,
  visibleNodeIds: Set<string>
): Particle[] {
  const active: Particle[] = [];

  for (const particle of particles) {
    const fromNode = nodes.get(particle.fromNodeId);
    const toNode = nodes.get(particle.toNodeId);
    const isVisible =
      visibleNodeIds.has(particle.fromNodeId) && visibleNodeIds.has(particle.toNodeId);

    particle.progress += particle.speed;

    if (particle.progress > 1) continue;
    active.push(particle);

    if (!isVisible || !fromNode?.x || !toNode?.x || fromNode.y == null || toNode.y == null)
      continue;
    if (particle.progress < 0) continue;

    const t = particle.progress;
    const x = fromNode.x + (toNode.x - fromNode.x) * t;
    const y = fromNode.y + (toNode.y - fromNode.y) * t;

    // Glow
    ctx.fillStyle = particle.color + '40';
    ctx.beginPath();
    ctx.arc(x, y, 14, 0, Math.PI * 2);
    ctx.fill();

    // Circle
    ctx.fillStyle = particle.color;
    ctx.beginPath();
    ctx.arc(x, y, 10, 0, Math.PI * 2);
    ctx.fill();

    // Label
    ctx.fillStyle = '#ffffff';
    ctx.font = 'bold 8px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(particle.label, x, y);
  }

  return active;
}

function renderNodes(
  ctx: CanvasRenderingContext2D,
  nodes: GraphNode[],
  hoveredNodeId: string | null
) {
  for (const node of nodes) {
    if (node.x == null || node.y == null) continue;

    // Emoji
    const emoji =
      node.type === 'self'
        ? '🟢'
        : node.type === 'repeater'
          ? '📡'
          : node.isAmbiguous
            ? '❓'
            : '👤';
    const size = node.type === 'self' ? 36 : 18;

    ctx.font = `${size}px sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(emoji, node.x, node.y);

    // Label
    const label = node.isAmbiguous
      ? node.id
      : node.name || (node.type === 'self' ? 'Me' : node.id.slice(0, 8));
    ctx.font = '11px sans-serif';
    ctx.textBaseline = 'top';
    ctx.fillStyle = node.isAmbiguous ? COLORS.ambiguous : '#e5e7eb';
    ctx.fillText(label, node.x, node.y + size / 2 + 4);

    // Ambiguous names
    if (node.isAmbiguous && node.ambiguousNames?.length) {
      ctx.font = '9px sans-serif';
      ctx.fillStyle = '#6b7280';
      let yOffset = node.y + size / 2 + 18;

      if (hoveredNodeId === node.id) {
        for (const name of node.ambiguousNames) {
          ctx.fillText(name, node.x, yOffset);
          yOffset += 11;
        }
      } else if (node.ambiguousNames.length === 1) {
        ctx.fillText(node.ambiguousNames[0], node.x, yOffset);
      } else {
        ctx.fillText(
          `${node.ambiguousNames[0]} +${node.ambiguousNames.length - 1} more`,
          node.x,
          yOffset
        );
      }
    }
  }
}

// =============================================================================
// MAIN COMPONENT
// =============================================================================

interface PacketVisualizerProps {
  packets: RawPacket[];
  contacts: Contact[];
  config: RadioConfig | null;
  fullScreen?: boolean;
  onFullScreenChange?: (fullScreen: boolean) => void;
  onClearPackets?: () => void;
}

export function PacketVisualizer({
  packets,
  contacts,
  config,
  fullScreen,
  onFullScreenChange,
  onClearPackets,
}: PacketVisualizerProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 });

  // Options
  const [showAmbiguousPaths, setShowAmbiguousPaths] = useState(true);
  const [showAmbiguousNodes, setShowAmbiguousNodes] = useState(false);
  const [splitAmbiguousByTraffic, setSplitAmbiguousByTraffic] = useState(false);
  const [chargeStrength, setChargeStrength] = useState(-200);
  const [filterOldRepeaters, setFilterOldRepeaters] = useState(false);
  const [observationWindowSec, setObservationWindowSec] = useState(DEFAULT_OBSERVATION_WINDOW_SEC);
  const [letEmDrift, setLetEmDrift] = useState(true);
  const [particleSpeedMultiplier, setParticleSpeedMultiplier] = useState(2);
  const [hideUI, setHideUI] = useState(false);

  // Pan/zoom
  const [transform, setTransform] = useState({ x: 0, y: 0, scale: 1 });
  const isDraggingRef = useRef(false);
  const lastMouseRef = useRef({ x: 0, y: 0 });
  const draggedNodeRef = useRef<GraphNode | null>(null);

  // Hover
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);

  // Data layer
  const data = useVisualizerData({
    packets,
    contacts,
    config,
    showAmbiguousPaths,
    showAmbiguousNodes,
    splitAmbiguousByTraffic,
    chargeStrength,
    letEmDrift,
    particleSpeedMultiplier,
    observationWindowSec,
    dimensions,
  });

  // Track dimensions
  useEffect(() => {
    const update = () => {
      if (containerRef.current) {
        const rect = containerRef.current.getBoundingClientRect();
        setDimensions({ width: rect.width, height: rect.height });
      }
    };
    update();
    const observer = new ResizeObserver(update);
    if (containerRef.current) observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, []);

  // Render
  const render = useCallback(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return;

    const { width, height } = dimensions;
    const dpr = window.devicePixelRatio || 1;

    canvas.width = width * dpr;
    canvas.height = height * dpr;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    ctx.scale(dpr, dpr);

    ctx.fillStyle = COLORS.background;
    ctx.fillRect(0, 0, width, height);

    ctx.save();
    ctx.translate(width / 2, height / 2);
    ctx.scale(transform.scale, transform.scale);
    ctx.translate(transform.x - width / 2, transform.y - height / 2);

    const now = Date.now();
    const allNodes = Array.from(data.nodes.values());
    const visibleNodeIds = new Set<string>();

    // Filter nodes
    const visibleNodes = allNodes.filter((node) => {
      if (node.type === 'self' || node.type === 'client') {
        visibleNodeIds.add(node.id);
        return true;
      }
      if (filterOldRepeaters && node.type === 'repeater') {
        const lastTime = node.lastSeen ? node.lastSeen * 1000 : node.lastActivity;
        if (now - lastTime > FORTY_EIGHT_HOURS_MS) return false;
      }
      visibleNodeIds.add(node.id);
      return true;
    });

    // Filter links
    const allLinks = Array.from(data.links.values());
    const visibleLinks = allLinks.filter((link) => {
      const { sourceId, targetId } = getLinkId(link);
      return visibleNodeIds.has(sourceId) && visibleNodeIds.has(targetId);
    });

    renderLinks(ctx, visibleLinks, data.nodes);
    data.particles.splice(
      0,
      data.particles.length,
      ...renderParticles(ctx, data.particles, data.nodes, visibleNodeIds)
    );
    renderNodes(ctx, visibleNodes, hoveredNodeId);

    ctx.restore();
  }, [dimensions, transform, data, hoveredNodeId, filterOldRepeaters]);

  // Animation loop
  useEffect(() => {
    let running = true;
    const animate = () => {
      if (!running) return;
      render();
      requestAnimationFrame(animate);
    };
    animate();
    return () => {
      running = false;
    };
  }, [render]);

  // Mouse handlers
  const screenToGraph = useCallback(
    (screenX: number, screenY: number) => {
      const { width, height } = dimensions;
      const cx = (screenX - width / 2) / transform.scale - transform.x + width / 2;
      const cy = (screenY - height / 2) / transform.scale - transform.y + height / 2;
      return { x: cx, y: cy };
    },
    [dimensions, transform]
  );

  const findNodeAt = useCallback(
    (gx: number, gy: number) => {
      for (const node of data.nodes.values()) {
        if (node.x == null || node.y == null) continue;
        if (Math.hypot(gx - node.x, gy - node.y) < 20) return node;
      }
      return null;
    },
    [data.nodes]
  );

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      const canvas = canvasRef.current;
      if (!canvas) return;

      const rect = canvas.getBoundingClientRect();
      const pos = screenToGraph(e.clientX - rect.left, e.clientY - rect.top);
      const node = findNodeAt(pos.x, pos.y);

      if (node) {
        // Start dragging this node
        draggedNodeRef.current = node;
        // Fix the node's position while dragging
        node.fx = node.x;
        node.fy = node.y;
        // Reheat simulation slightly for responsive feedback
        data.simulation?.alpha(0.3).restart();
      } else {
        // Start panning
        isDraggingRef.current = true;
      }
      lastMouseRef.current = { x: e.clientX, y: e.clientY };
    },
    [screenToGraph, findNodeAt, data.simulation]
  );

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      const canvas = canvasRef.current;
      if (!canvas) return;

      const rect = canvas.getBoundingClientRect();
      const pos = screenToGraph(e.clientX - rect.left, e.clientY - rect.top);

      // Update hover state
      setHoveredNodeId(findNodeAt(pos.x, pos.y)?.id || null);

      // Handle node dragging
      if (draggedNodeRef.current) {
        draggedNodeRef.current.fx = pos.x;
        draggedNodeRef.current.fy = pos.y;
        return;
      }

      // Handle canvas panning
      if (!isDraggingRef.current) return;
      const dx = e.clientX - lastMouseRef.current.x;
      const dy = e.clientY - lastMouseRef.current.y;
      lastMouseRef.current = { x: e.clientX, y: e.clientY };
      setTransform((t) => ({ ...t, x: t.x + dx / t.scale, y: t.y + dy / t.scale }));
    },
    [screenToGraph, findNodeAt]
  );

  const handleMouseUp = useCallback(() => {
    if (draggedNodeRef.current) {
      // Release the node - clear fixed position so it can move freely again
      draggedNodeRef.current.fx = null;
      draggedNodeRef.current.fy = null;
      draggedNodeRef.current = null;
    }
    isDraggingRef.current = false;
  }, []);

  const handleMouseLeave = useCallback(() => {
    if (draggedNodeRef.current) {
      draggedNodeRef.current.fx = null;
      draggedNodeRef.current.fy = null;
      draggedNodeRef.current = null;
    }
    isDraggingRef.current = false;
    setHoveredNodeId(null);
  }, []);

  const handleWheel = useCallback((e: WheelEvent) => {
    e.preventDefault();
    const factor = e.deltaY > 0 ? 1 / 1.1 : 1.1;
    setTransform((t) => ({ ...t, scale: Math.min(Math.max(t.scale * factor, 0.1), 5) }));
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    canvas.addEventListener('wheel', handleWheel, { passive: false });
    return () => canvas.removeEventListener('wheel', handleWheel);
  }, [handleWheel]);

  // Determine cursor based on state
  const getCursor = () => {
    if (draggedNodeRef.current) return 'grabbing';
    if (hoveredNodeId) return 'pointer';
    return 'grab';
  };

  return (
    <div ref={containerRef} className="w-full h-full bg-background relative overflow-hidden">
      <canvas
        ref={canvasRef}
        className="w-full h-full"
        style={{ display: 'block', cursor: getCursor() }}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseLeave}
      />

      {/* Legend */}
      {!hideUI && (
        <div className="absolute bottom-4 left-4 bg-background/80 backdrop-blur-sm rounded-lg p-3 text-xs border border-border">
          <div className="flex gap-6">
            <div className="flex flex-col gap-1.5">
              <div className="text-muted-foreground font-medium mb-1">Nodes</div>
              {LEGEND_ITEMS.map((item) => (
                <div key={item.label} className="flex items-center gap-2">
                  <span className={item.size}>{item.emoji}</span>
                  <span>{item.label}</span>
                </div>
              ))}
            </div>
            <div className="flex flex-col gap-1.5">
              <div className="text-muted-foreground font-medium mb-1">Packets</div>
              {PACKET_LEGEND_ITEMS.map((item) => (
                <div key={item.label} className="flex items-center gap-2">
                  <div
                    className="w-5 h-5 rounded-full flex items-center justify-center text-[8px] font-bold text-white"
                    style={{ backgroundColor: item.color }}
                  >
                    {item.label}
                  </div>
                  <span>{item.description}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Options */}
      <div className="absolute top-4 right-4 bg-background/80 backdrop-blur-sm rounded-lg p-3 text-xs border border-border">
        <div className="flex flex-col gap-2">
          {!hideUI && (
            <>
              <div>Nodes: {data.stats.nodes}</div>
              <div>Links: {data.stats.links}</div>
              <div className="border-t border-border pt-2 mt-1 flex flex-col gap-2">
                <label className="flex items-center gap-2 cursor-pointer">
                  <Checkbox
                    checked={showAmbiguousPaths}
                    onCheckedChange={(c) => setShowAmbiguousPaths(c === true)}
                  />
                  <span title="Show placeholder nodes for repeaters when the 1-byte prefix matches multiple contacts">
                    Ambiguous repeaters
                  </span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <Checkbox
                    checked={showAmbiguousNodes}
                    onCheckedChange={(c) => setShowAmbiguousNodes(c === true)}
                  />
                  <span title="Show placeholder nodes for senders/recipients when only a 1-byte prefix is known">
                    Ambiguous sender/recipient
                  </span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <Checkbox
                    checked={splitAmbiguousByTraffic}
                    onCheckedChange={(c) => setSplitAmbiguousByTraffic(c === true)}
                    disabled={!showAmbiguousPaths}
                  />
                  <span
                    title="Split ambiguous repeaters into separate nodes based on traffic patterns (prev→next). Helps identify colliding prefixes representing different physical nodes."
                    className={!showAmbiguousPaths ? 'text-muted-foreground' : ''}
                  >
                    Hueristically group repeaters by traffic pattern
                  </span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <Checkbox
                    checked={filterOldRepeaters}
                    onCheckedChange={(c) => setFilterOldRepeaters(c === true)}
                  />
                  <span title="Hide repeaters not heard within the last 48 hours">
                    Hide repeaters &gt;48hrs heard
                  </span>
                </label>
                <div className="flex items-center gap-2">
                  <label
                    className="text-muted-foreground"
                    title="How long to wait for duplicate packets via different paths before animating"
                  >
                    Observation window:
                  </label>
                  <input
                    type="number"
                    min="1"
                    max="60"
                    value={observationWindowSec}
                    onChange={(e) =>
                      setObservationWindowSec(
                        Math.max(1, Math.min(60, parseInt(e.target.value) || 1))
                      )
                    }
                    className="w-12 px-1 py-0.5 bg-background border border-border rounded text-xs text-center"
                  />
                  <span className="text-muted-foreground">sec</span>
                </div>
                <div className="border-t border-border pt-2 mt-1 flex flex-col gap-2">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <Checkbox
                      checked={letEmDrift}
                      onCheckedChange={(c) => setLetEmDrift(c === true)}
                    />
                    <span title="When enabled, the graph continuously reorganizes itself into a better layout">
                      Let &apos;em drift
                    </span>
                  </label>
                  <div className="flex flex-col gap-1 mt-1">
                    <label
                      className="text-muted-foreground"
                      title="How strongly nodes repel each other. Higher values spread nodes out more."
                    >
                      Repulsion: {Math.abs(chargeStrength)}
                    </label>
                    <input
                      type="range"
                      min="50"
                      max="2500"
                      value={Math.abs(chargeStrength)}
                      onChange={(e) => setChargeStrength(-parseInt(e.target.value))}
                      className="w-full h-2 bg-border rounded-lg appearance-none cursor-pointer accent-primary"
                    />
                  </div>
                  <div className="flex flex-col gap-1 mt-1">
                    <label
                      className="text-muted-foreground"
                      title="How fast particles travel along links. Higher values make packets move faster."
                    >
                      Packet speed: {particleSpeedMultiplier}x
                    </label>
                    <input
                      type="range"
                      min="1"
                      max="5"
                      step="0.5"
                      value={particleSpeedMultiplier}
                      onChange={(e) => setParticleSpeedMultiplier(parseFloat(e.target.value))}
                      className="w-full h-2 bg-border rounded-lg appearance-none cursor-pointer accent-primary"
                    />
                  </div>
                </div>
                <button
                  onClick={data.randomizePositions}
                  className="mt-2 px-3 py-1.5 bg-primary/20 hover:bg-primary/30 text-primary rounded text-xs transition-colors"
                  title="Randomize node positions and let the simulation settle into a new layout"
                >
                  Shuffle layout
                </button>
                <button
                  onClick={data.expandContract}
                  className="mt-1 px-3 py-1.5 bg-primary/20 hover:bg-primary/30 text-primary rounded text-xs transition-colors"
                  title="Expand nodes apart then contract back - can help untangle the graph"
                >
                  Oooh Big Stretch!
                </button>
                <button
                  onClick={() => {
                    data.clearAndReset();
                    onClearPackets?.();
                  }}
                  className="mt-1 px-3 py-1.5 bg-yellow-500/20 hover:bg-yellow-500/30 text-yellow-500 rounded text-xs transition-colors"
                  title="Clear all nodes, links, and packets - reset to initial state"
                >
                  Clear &amp; Reset
                </button>
              </div>
            </>
          )}
          <div className={hideUI ? '' : 'border-t border-border pt-2 mt-1 flex flex-col gap-2'}>
            <label className="flex items-center gap-2 cursor-pointer">
              <Checkbox checked={hideUI} onCheckedChange={(c) => setHideUI(c === true)} />
              <span title="Hide legends and controls for a cleaner view">Hide UI</span>
            </label>
            {onFullScreenChange && (
              <label className="flex items-center gap-2 cursor-pointer">
                <Checkbox
                  checked={fullScreen}
                  onCheckedChange={(c) => onFullScreenChange(c === true)}
                />
                <span title="Hide the packet feed panel">Full screen</span>
              </label>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
