import { useState } from 'react';
import { Menu } from 'lucide-react';
import type { HealthStatus, RadioConfig } from '../types';
import { api } from '../api';
import { toast } from './ui/sonner';

interface StatusBarProps {
  health: HealthStatus | null;
  config: RadioConfig | null;
  onConfigClick: () => void;
  onAdvertise: () => void;
  onMenuClick?: () => void;
}

export function StatusBar({ health, config, onConfigClick, onAdvertise, onMenuClick }: StatusBarProps) {
  const connected = health?.radio_connected ?? false;
  const [reconnecting, setReconnecting] = useState(false);

  const handleReconnect = async () => {
    setReconnecting(true);
    try {
      const result = await api.reconnectRadio();
      if (result.connected) {
        toast.success('Reconnected', { description: result.message });
      }
    } catch (err) {
      toast.error('Reconnection failed', {
        description: err instanceof Error ? err.message : 'Check radio connection and power',
      });
    } finally {
      setReconnecting(false);
    }
  };

  return (
    <div className="flex items-center gap-4 px-4 py-2 bg-[#252525] border-b border-[#333] text-xs">
      {/* Mobile menu button - only visible on small screens */}
      {onMenuClick && (
        <button
          onClick={onMenuClick}
          className="md:hidden p-1 bg-transparent border-none text-[#e0e0e0] cursor-pointer"
          aria-label="Open menu"
        >
          <Menu className="h-5 w-5" />
        </button>
      )}

      <h1 className="hidden lg:block text-base font-semibold mr-auto">RemoteTerm</h1>

      <div className="flex items-center gap-1 text-[#888]">
        <div className={`w-2 h-2 rounded-full ${connected ? 'bg-[#4caf50]' : 'bg-[#666]'}`} />
        <span className="hidden lg:inline text-[#e0e0e0]">{connected ? 'Connected' : 'Disconnected'}</span>
      </div>

      {health?.serial_port && (
        <div className="hidden xl:flex items-center gap-1 text-[#888]">
          Port: <span className="text-[#e0e0e0]">{health.serial_port}</span>
        </div>
      )}

      {config && (
        <>
          <div className="hidden lg:flex items-center gap-1 text-[#888]">
            Name: <span className="text-[#e0e0e0]">{config.name || 'Unnamed'}</span>
          </div>
          <div className="hidden xl:flex items-center gap-1 text-[#888]">
            Freq: <span className="text-[#e0e0e0]">{config.radio.freq} MHz</span>
          </div>
          <div className="hidden xl:flex items-center gap-1 text-[#888]">
            SF{config.radio.sf}/CR{config.radio.cr}
          </div>
          <div className="hidden xl:flex items-center gap-1 text-[#888]">
            TX: <span className="text-[#e0e0e0]">{config.tx_power} dBm</span>
          </div>
        </>
      )}

      {/* Spacer to push buttons right on mobile */}
      <div className="flex-1 lg:hidden" />

      {!connected && (
        <button
          onClick={handleReconnect}
          disabled={reconnecting}
          className="px-3 py-1 bg-[#4a3000] border border-[#6b4500] text-[#ffa500] rounded text-xs cursor-pointer hover:bg-[#5a3a00] disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {reconnecting ? 'Reconnecting...' : 'Reconnect'}
        </button>
      )}
      <button
        onClick={onAdvertise}
        disabled={!connected}
        className="px-3 py-1 bg-[#333] border border-[#444] text-[#e0e0e0] rounded text-xs cursor-pointer hover:bg-[#444] disabled:bg-[#333] disabled:text-[#666] disabled:cursor-not-allowed"
      >
        Advertise
      </button>
      <button
        onClick={onConfigClick}
        className="px-3 py-1 bg-[#333] border border-[#444] text-[#e0e0e0] rounded text-xs cursor-pointer hover:bg-[#444]"
      >
        Config
      </button>
    </div>
  );
}
