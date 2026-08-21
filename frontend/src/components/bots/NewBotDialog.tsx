import { useEffect, useMemo, useState } from 'react';
import { Search as SearchIcon } from 'lucide-react';

import { api } from '../../api';
import type { Bot, BotLibraryEntry } from '../../types';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { toast } from '../ui/sonner';
import { cn } from '@/lib/utils';

const TEMPLATES = [
  {
    id: 'blank',
    name: 'Blank bot',
    description: 'Empty scaffold with one on_keyword handler and API comments.',
  },
  {
    id: 'library',
    name: 'From library',
    description: 'Clone a built-in as your starting point — an independent copy.',
  },
] as const;

type TemplateId = (typeof TEMPLATES)[number]['id'];

export function NewBotDialogBody({ onCreated }: { onCreated: (bot: Bot) => void }) {
  const [name, setName] = useState('');
  const [template, setTemplate] = useState<TemplateId>('blank');
  const [library, setLibrary] = useState<BotLibraryEntry[]>([]);
  const [libraryFilter, setLibraryFilter] = useState('');
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    if (template !== 'library' || library.length > 0) return;
    api
      .getBotLibrary()
      .then(setLibrary)
      .catch((err: unknown) =>
        toast.error('Failed to load library', {
          description: err instanceof Error ? err.message : undefined,
        })
      );
  }, [template, library.length]);

  const visibleLibrary = useMemo(() => {
    const query = libraryFilter.trim().toLowerCase();
    if (!query) return library;
    return library.filter(
      (entry) =>
        entry.name.toLowerCase().includes(query) ||
        entry.description.toLowerCase().includes(query) ||
        entry.category.toLowerCase().includes(query)
    );
  }, [library, libraryFilter]);

  const handleCreate = async () => {
    const trimmed = name.trim();
    if (!trimmed) {
      toast.error('Give the bot a name');
      return;
    }
    if (template === 'library' && !selectedKey) {
      toast.error('Pick a library bot to clone');
      return;
    }
    setCreating(true);
    try {
      const bot = await api.createBot({
        name: trimmed,
        from_builtin_key: template === 'library' ? selectedKey : null,
      });
      toast.success(`Bot ${bot.name} created`);
      onCreated(bot);
    } catch (err) {
      toast.error('Failed to create bot', {
        description: err instanceof Error ? err.message : undefined,
      });
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="px-5 py-4 flex flex-col gap-4">
      <div>
        <div className="text-xs text-muted-foreground mb-1.5">Name</div>
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. tide-report"
          autoFocus
        />
      </div>

      <div>
        <div className="text-xs text-muted-foreground mb-1.5">Start from</div>
        <div className="grid grid-cols-2 gap-2">
          {TEMPLATES.map((tpl) => (
            <button
              key={tpl.id}
              type="button"
              onClick={() => setTemplate(tpl.id)}
              className={cn(
                'text-left border rounded-lg px-3 py-2.5 transition-colors',
                template === tpl.id
                  ? 'border-primary/55 bg-primary/5'
                  : 'border-input hover:bg-accent'
              )}
            >
              <div className="text-[0.8125rem] font-medium">{tpl.name}</div>
              <div className="text-[0.6875rem] text-muted-foreground mt-0.5 leading-relaxed">
                {tpl.description}
              </div>
            </button>
          ))}
        </div>
      </div>

      {template === 'library' && (
        <div className="border border-border rounded-md overflow-hidden">
          <div className="flex items-center gap-2 px-3 py-2 bg-muted">
            <SearchIcon className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
            <input
              value={libraryFilter}
              onChange={(e) => setLibraryFilter(e.target.value)}
              placeholder="Search the built-in library…"
              className="flex-1 bg-transparent text-xs outline-none placeholder:text-muted-foreground"
            />
          </div>
          <div className="max-h-56 overflow-y-auto">
            {visibleLibrary.map((entry) => (
              <button
                key={entry.key}
                type="button"
                onClick={() => setSelectedKey(entry.key)}
                className={cn(
                  'w-full flex items-center gap-2.5 px-3 py-2 border-t border-border/50 text-left transition-colors',
                  selectedKey === entry.key ? 'bg-accent' : 'hover:bg-accent/50'
                )}
              >
                <span className="text-[0.8125rem] font-medium">{entry.name}</span>
                <span className="text-[0.625rem] uppercase tracking-wider bg-muted text-muted-foreground rounded px-1.5 py-0.5">
                  {entry.category}
                </span>
                <span className="flex-1 text-[0.6875rem] text-muted-foreground truncate">
                  {entry.description}
                </span>
                {entry.installed && (
                  <span className="text-[0.625rem] uppercase tracking-wider bg-primary/15 text-primary rounded px-1.5 py-0.5">
                    installed
                  </span>
                )}
              </button>
            ))}
            {visibleLibrary.length === 0 && (
              <div className="px-3 py-4 text-xs text-muted-foreground">No matches.</div>
            )}
          </div>
        </div>
      )}

      <div className="flex items-center gap-2.5 pt-1">
        <span className="flex-1 text-[0.6875rem] text-warning">
          New bots start disabled until you enable them.
        </span>
        <Button size="sm" onClick={() => void handleCreate()} disabled={creating}>
          {creating ? 'Creating…' : 'Create Bot'}
        </Button>
      </div>
    </div>
  );
}
