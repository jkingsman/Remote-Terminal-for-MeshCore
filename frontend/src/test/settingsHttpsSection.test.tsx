import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { SettingsHttpsSection } from '../components/settings/SettingsHttpsSection';

describe('HTTPS settings', () => {
  it('shows HTTP status and honest self-signed guidance', () => {
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: false });
    render(<SettingsHttpsSection />);
    expect(screen.getByText(/Current status:/).parentElement).toHaveTextContent('HTTP');
    expect(screen.getByText(/self-signed certificate works/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /HTTPS setup guide/i })).toBeInTheDocument();
  });
});
