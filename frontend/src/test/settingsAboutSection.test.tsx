import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SettingsAboutSection } from '../components/settings/SettingsAboutSection';

describe('SettingsAboutSection', () => {
  beforeEach(() => {
    vi.stubGlobal('__APP_VERSION__', '3.2.0-test');
    vi.stubGlobal('__COMMIT_HASH__', 'deadbeef');
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders the debug support snapshot link', () => {
    render(<SettingsAboutSection />);

    const link = screen.getByRole('link', { name: /Open debug support snapshot/i });
    expect(link).toHaveAttribute('href', '/api/debug');
    expect(link).toHaveAttribute('target', '_blank');
  });
});
