import { Separator } from '../ui/separator';

const GITHUB_URL = 'https://github.com/jkingsman/Remote-Terminal-for-MeshCore';

export function SettingsAboutSection({ className }: { className?: string }) {
  const version = __APP_VERSION__;
  const commit = __COMMIT_HASH__;

  return (
    <div className={className}>
      <div className="space-y-6">
        {/* Version */}
        <div className="text-center space-y-1">
          <h3 className="text-lg font-semibold">RemoteTerm for MeshCore</h3>
          <div className="text-sm text-muted-foreground">
            v{version}
            <span className="mx-1.5">·</span>
            <span className="font-mono text-xs">{commit}</span>
          </div>
        </div>

        <Separator />

        {/* Author & License */}
        <div className="text-sm text-center space-y-2">
          <p>
            Made with love and open source by{' '}
            <a
              href="https://jacksbrain.com"
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary hover:underline"
            >
              Jack Kingsman
            </a>
          </p>
          <p>
            Licensed under the{' '}
            <a
              href={`${GITHUB_URL}/blob/main/LICENSE.md`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary hover:underline"
            >
              MIT License
            </a>
          </p>
          <p>
            This code is free, and ad-free, forever. If you love my work,{' '}
            <a
              href="https://ko-fi.com/jackkingsman"
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary hover:underline"
            >
              buy me a coffee!
            </a>
          </p>
        </div>

        <Separator />

        {/* Links */}
        <div className="flex justify-center gap-4 text-sm">
          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary hover:underline"
          >
            GitHub
          </a>
          <a
            href={`${GITHUB_URL}/issues`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary hover:underline"
          >
            Report a Bug
          </a>
          <a
            href={`${GITHUB_URL}/blob/main/CHANGELOG.md`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary hover:underline"
          >
            Changelog
          </a>
        </div>

        <Separator />

        {/* Acknowledgements */}
        <div className="text-sm text-center text-muted-foreground space-y-2">
          <p>With great appreciation to those who have made the tools upon which this is built:</p>
          <p>
            <a
              href="https://github.com/meshcore-dev/MeshCore"
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary hover:underline"
            >
              MeshCore
            </a>
            <span className="mx-1.5">·</span>
            <a
              href="https://github.com/meshcore-dev/meshcore_py"
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary hover:underline"
            >
              meshcore_py
            </a>
          </p>
        </div>

        <Separator />

        <div className="text-center">
          <a
            href="/api/debug"
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-muted-foreground hover:text-primary hover:underline"
          >
            Open debug support snapshot
          </a>
        </div>
      </div>
    </div>
  );
}
