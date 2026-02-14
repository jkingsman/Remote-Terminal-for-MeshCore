import type { Contact, RadioConfig, MessagePath } from '../types';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from './ui/dialog';
import { Button } from './ui/button';
import {
  resolvePath,
  parsePathHops,
  calculateDistance,
  isValidLocation,
  formatDistance,
  type SenderInfo,
  type ResolvedPath,
  type PathHop,
} from '../utils/pathUtils';
import { formatTime } from '../utils/messageParser';
import { getMapFocusHash } from '../utils/urlHash';

interface PathModalProps {
  open: boolean;
  onClose: () => void;
  paths: MessagePath[];
  senderInfo: SenderInfo;
  contacts: Contact[];
  config: RadioConfig | null;
  onSendAgain?: () => void;
  canSendAgain?: boolean;
}

export function PathModal({
  open,
  onClose,
  paths,
  senderInfo,
  contacts,
  config,
  onSendAgain,
  canSendAgain = false,
}: PathModalProps) {
  // Resolve all paths
  const resolvedPaths = paths.map((p) => ({
    ...p,
    resolved: resolvePath(p.path, senderInfo, contacts, config),
  }));

  const hasSinglePath = paths.length === 1;

  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && onClose()}>
      <DialogContent className="max-w-md max-h-[80vh] flex flex-col">
        <DialogHeader>
          <DialogTitle>Message Path{!hasSinglePath && `s (${paths.length})`}</DialogTitle>
          <DialogDescription>
            {hasSinglePath ? (
              <>
                This shows <em>one route</em> that this message traveled through the mesh network.
                Routers may be incorrectly identified due to prefix collisions between heard and
                non-heard router advertisements.
              </>
            ) : (
              <>
                This message was received via <strong>{paths.length} different routes</strong>.
                Routers may be incorrectly identified due to prefix collisions.
              </>
            )}
          </DialogDescription>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto py-2 space-y-4">
          {/* Raw path summary */}
          <div className="text-sm">
            {paths.map((p, index) => {
              const hops = parsePathHops(p.path);
              const rawPath = hops.length > 0 ? hops.join('->') : 'direct';
              return (
                <div key={index}>
                  <span className="text-foreground/70 font-semibold">Path {index + 1}:</span>{' '}
                  <span className="font-mono text-muted-foreground">{rawPath}</span>
                </div>
              );
            })}
          </div>

          {/* Straight-line distance (sender to receiver, same for all routes) */}
          {resolvedPaths.length > 0 &&
            isValidLocation(
              resolvedPaths[0].resolved.sender.lat,
              resolvedPaths[0].resolved.sender.lon
            ) &&
            isValidLocation(
              resolvedPaths[0].resolved.receiver.lat,
              resolvedPaths[0].resolved.receiver.lon
            ) && (
              <div className="text-sm pb-2 border-b border-border">
                <span className="text-muted-foreground">Straight-line distance: </span>
                <span className="font-medium">
                  {formatDistance(
                    calculateDistance(
                      resolvedPaths[0].resolved.sender.lat,
                      resolvedPaths[0].resolved.sender.lon,
                      resolvedPaths[0].resolved.receiver.lat,
                      resolvedPaths[0].resolved.receiver.lon
                    )!
                  )}
                </span>
              </div>
            )}

          {resolvedPaths.map((pathData, index) => (
            <div key={index}>
              {!hasSinglePath && (
                <div className="text-sm text-foreground/70 font-semibold mb-2 pb-1 border-b border-border">
                  Path {index + 1}{' '}
                  <span className="font-normal text-muted-foreground">
                    — received {formatTime(pathData.received_at)}
                  </span>
                </div>
              )}
              <PathVisualization resolved={pathData.resolved} senderInfo={senderInfo} />
            </div>
          ))}
        </div>

        <DialogFooter>
          {canSendAgain && onSendAgain && (
            <Button variant="outline" onClick={onSendAgain}>
              Send Again
            </Button>
          )}
          <Button onClick={onClose}>Close</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

interface PathVisualizationProps {
  resolved: ResolvedPath;
  senderInfo: SenderInfo;
}

function PathVisualization({ resolved, senderInfo }: PathVisualizationProps) {
  // Track previous location for each hop to calculate distances
  // Returns null if previous hop was ambiguous or has invalid location
  const getPrevLocation = (hopIndex: number): { lat: number | null; lon: number | null } | null => {
    if (hopIndex === 0) {
      // Check if sender has valid location
      if (!isValidLocation(resolved.sender.lat, resolved.sender.lon)) {
        return null;
      }
      return { lat: resolved.sender.lat, lon: resolved.sender.lon };
    }
    const prevHop = resolved.hops[hopIndex - 1];
    // If previous hop was ambiguous, we can't show meaningful distances
    if (prevHop.matches.length > 1) {
      return null;
    }
    // If previous hop was unknown, we also can't calculate
    if (prevHop.matches.length === 0) {
      return null;
    }
    // Check if previous hop has valid location
    if (isValidLocation(prevHop.matches[0].lat, prevHop.matches[0].lon)) {
      return { lat: prevHop.matches[0].lat, lon: prevHop.matches[0].lon };
    }
    return null;
  };

  return (
    <div className="space-y-0">
      {/* Sender */}
      <PathNode
        label="Sender"
        name={resolved.sender.name}
        prefix={resolved.sender.prefix}
        distance={null}
        isFirst
        lat={resolved.sender.lat}
        lon={resolved.sender.lon}
        publicKey={senderInfo.publicKeyOrPrefix}
      />

      {/* Hops */}
      {resolved.hops.map((hop, index) => (
        <HopNode
          key={index}
          hop={hop}
          hopNumber={index + 1}
          prevLocation={getPrevLocation(index)}
        />
      ))}

      {/* Receiver */}
      <PathNode
        label="Receiver (me)"
        name={resolved.receiver.name}
        prefix={resolved.receiver.prefix}
        distance={calculateReceiverDistance(resolved)}
        isLast
        lat={resolved.receiver.lat}
        lon={resolved.receiver.lon}
        publicKey={resolved.receiver.publicKey ?? undefined}
      />

      {/* Total distance */}
      {resolved.totalDistances && resolved.totalDistances.length > 0 && (
        <div className="pt-3 mt-3 border-t border-border">
          <span className="text-sm text-muted-foreground">
            Presumed unambiguous distance covered:{' '}
          </span>
          <span className="text-sm font-medium">
            {resolved.hasGaps ? '>' : ''}
            {formatDistance(resolved.totalDistances[0])}
          </span>
        </div>
      )}
    </div>
  );
}

interface PathNodeProps {
  label: string;
  name: string;
  prefix: string;
  distance: number | null;
  isFirst?: boolean;
  isLast?: boolean;
  /** Optional coordinates for map link */
  lat?: number | null;
  lon?: number | null;
  /** Public key for map focus link (required if lat/lon provided) */
  publicKey?: string;
}

function PathNode({
  label,
  name,
  prefix,
  distance,
  isFirst,
  isLast,
  lat,
  lon,
  publicKey,
}: PathNodeProps) {
  const hasLocation = isValidLocation(lat ?? null, lon ?? null) && publicKey;

  return (
    <div className="flex gap-3">
      {/* Vertical line and dot column */}
      <div className="flex flex-col items-center w-4 flex-shrink-0">
        {!isFirst && <div className="w-0.5 h-3 bg-border" />}
        <div className="w-3 h-3 rounded-full bg-primary flex-shrink-0" />
        {!isLast && <div className="w-0.5 flex-1 bg-border" />}
      </div>

      {/* Content */}
      <div className="pb-3 flex-1 min-w-0">
        <div className="text-sm font-semibold">
          <span className="text-primary">{label}:</span>{' '}
          <span className="text-primary font-mono">{prefix}</span>
        </div>
        <div className="font-medium truncate">
          {name}
          {distance !== null && (
            <span className="text-xs text-muted-foreground ml-1">- {formatDistance(distance)}</span>
          )}
          {hasLocation && <CoordinateLink lat={lat!} lon={lon!} publicKey={publicKey!} />}
        </div>
      </div>
    </div>
  );
}

interface HopNodeProps {
  hop: PathHop;
  hopNumber: number;
  prevLocation: { lat: number | null; lon: number | null } | null;
}

function HopNode({ hop, hopNumber, prevLocation }: HopNodeProps) {
  const isAmbiguous = hop.matches.length > 1;
  const isUnknown = hop.matches.length === 0;

  // Calculate distance from previous location for a contact
  // Returns null if prev location unknown/ambiguous or contact has no valid location
  const getDistanceForContact = (contact: {
    lat: number | null;
    lon: number | null;
  }): number | null => {
    if (!prevLocation || prevLocation.lat === null || prevLocation.lon === null) {
      return null;
    }
    // Check if contact has valid location
    if (!isValidLocation(contact.lat, contact.lon)) {
      return null;
    }
    return calculateDistance(prevLocation.lat, prevLocation.lon, contact.lat, contact.lon);
  };

  return (
    <div className="flex gap-3">
      {/* Vertical line and dot column */}
      <div className="flex flex-col items-center w-4 flex-shrink-0">
        <div className="w-0.5 h-3 bg-border" />
        <div className="w-3 h-3 rounded-full bg-primary/50 flex-shrink-0" />
        <div className="w-0.5 flex-1 bg-border" />
      </div>

      {/* Content */}
      <div className="pb-3 flex-1 min-w-0">
        <div className="text-sm font-semibold">
          <span className="text-foreground/80">Hop {hopNumber}:</span>{' '}
          <span className="text-primary font-mono">{hop.prefix}</span>
          {isAmbiguous && <span className="text-yellow-500 ml-1 font-normal">(ambiguous)</span>}
        </div>

        {isUnknown ? (
          <div className="font-medium text-muted-foreground">&lt;UNKNOWN&gt;</div>
        ) : isAmbiguous ? (
          <div>
            {hop.matches.map((contact) => {
              const dist = getDistanceForContact(contact);
              const hasLocation = isValidLocation(contact.lat, contact.lon);
              return (
                <div key={contact.public_key} className="font-medium truncate">
                  {contact.name || contact.public_key.slice(0, 12)}
                  {dist !== null && (
                    <span className="text-xs text-muted-foreground ml-1">
                      - {formatDistance(dist)}
                    </span>
                  )}
                  {hasLocation && (
                    <CoordinateLink
                      lat={contact.lat!}
                      lon={contact.lon!}
                      publicKey={contact.public_key}
                    />
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          <div className="font-medium truncate">
            {hop.matches[0].name || hop.matches[0].public_key.slice(0, 12)}
            {hop.distanceFromPrev !== null && (
              <span className="text-xs text-muted-foreground ml-1">
                - {formatDistance(hop.distanceFromPrev)}
              </span>
            )}
            {isValidLocation(hop.matches[0].lat, hop.matches[0].lon) && (
              <CoordinateLink
                lat={hop.matches[0].lat!}
                lon={hop.matches[0].lon!}
                publicKey={hop.matches[0].public_key}
              />
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Render clickable coordinates that open the map focused on the contact
 */
function CoordinateLink({ lat, lon, publicKey }: { lat: number; lon: number; publicKey: string }) {
  const handleClick = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    // Open map in new tab with focus on this contact
    const url = window.location.origin + window.location.pathname + getMapFocusHash(publicKey);
    window.open(url, '_blank');
  };

  return (
    <span
      className="text-xs text-muted-foreground font-mono cursor-pointer hover:text-primary hover:underline ml-1"
      onClick={handleClick}
      title="View on map"
    >
      ({lat.toFixed(4)}, {lon.toFixed(4)})
    </span>
  );
}

function calculateReceiverDistance(resolved: ResolvedPath): number | null {
  // Get last hop's location (if any)
  let prevLat: number | null = null;
  let prevLon: number | null = null;

  if (resolved.hops.length > 0) {
    const lastHop = resolved.hops[resolved.hops.length - 1];
    // Only use last hop if it's unambiguous and has valid location
    if (
      lastHop.matches.length === 1 &&
      isValidLocation(lastHop.matches[0].lat, lastHop.matches[0].lon)
    ) {
      prevLat = lastHop.matches[0].lat;
      prevLon = lastHop.matches[0].lon;
    }
  } else {
    // No hops, calculate from sender to receiver (if sender has valid location)
    if (isValidLocation(resolved.sender.lat, resolved.sender.lon)) {
      prevLat = resolved.sender.lat;
      prevLon = resolved.sender.lon;
    }
  }

  if (prevLat === null || prevLon === null) {
    return null;
  }

  // Check receiver has valid location
  if (!isValidLocation(resolved.receiver.lat, resolved.receiver.lon)) {
    return null;
  }

  return calculateDistance(prevLat, prevLon, resolved.receiver.lat, resolved.receiver.lon);
}
