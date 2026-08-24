import { ExternalLink } from 'lucide-react';
import { cn } from '@/lib/utils';

export function SettingsHttpsSection({ className }: { className?: string }) {
  const secure = window.isSecureContext && window.location.protocol === 'https:';
  return (
    <div className={cn('space-y-4', className)}>
      <div>
        <h3 className="text-lg font-semibold">HTTPS / TLS</h3>
        <p className="text-sm text-muted-foreground">
          A secure context is required for browser microphone access.
        </p>
      </div>
      <div className="rounded-md border border-input p-4 text-sm space-y-2">
        <div>
          <span className="text-muted-foreground">Current status:</span> {secure ? 'HTTPS' : 'HTTP'}
        </div>
        <div>
          <span className="text-muted-foreground">Public hostname:</span> {window.location.hostname}
        </div>
        <div>
          <span className="text-muted-foreground">Certificate:</span>{' '}
          {secure ? 'Managed by the HTTPS endpoint' : 'Not active on this page'}
        </div>
        <div>
          <span className="text-muted-foreground">Certificate type:</span>{' '}
          {secure ? 'Proxy/server managed' : 'None'}
        </div>
        <div>
          <span className="text-muted-foreground">Certificate expiry:</span>{' '}
          {secure ? 'Not exposed by browser security APIs' : 'Not applicable'}
        </div>
      </div>
      {!secure && (
        <div className="rounded-md border border-warning/50 bg-warning/10 p-4 text-sm">
          RemoteTerm’s packaged Uvicorn service is intentionally unprivileged. Configure TLS on a
          standard reverse proxy (Caddy, nginx, or Apache), or start Uvicorn with explicitly managed
          certificate files. Direct local HTTP on port 8000 remains available.
        </div>
      )}
      <a
        href="https://github.com/jkingsman/Remote-Terminal-for-MeshCore/blob/main/README_ADVANCED.md#https--ssl"
        target="_blank"
        rel="noreferrer"
        className="inline-flex items-center gap-2 text-sm text-primary hover:underline"
      >
        HTTPS setup guide <ExternalLink size={14} />
      </a>
      <p className="text-xs text-muted-foreground">
        For a trusted public certificate, use a DNS hostname with your proxy’s Let’s Encrypt
        support. A self-signed certificate works without DNS but browsers warn until its certificate
        or CA is trusted manually.
      </p>
    </div>
  );
}
