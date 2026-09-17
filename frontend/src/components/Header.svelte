<script>
  import { ICONS } from '../icons.js';

  let {
    appVersion = 'v1.0.0',
    currentUser = '',
    sktUsername = '',
    unreadCount = 0,
    searchQuery = '',
    onMarkAllRead = () => {},
    onSearchChange = () => {},
    onClearSearch = () => {},
    onToggleTheme = () => {}
  } = $props();

  function handleInput(e) {
    onSearchChange(e.target.value);
  }
</script>

<header>
  <div class="brand-group">
    <a href="/" class="brand-logo">
      <h1>SkT Proxy</h1>
    </a>
    <span class="build-badge" title="Build Version">{appVersion}</span>
  </div>

  <div class="user-status-group">
    {#if currentUser}
      <span class="user-pill" title="Authenticated User">
        {@html ICONS.user}
        {currentUser}
      </span>
    {/if}
    <span class="user-pill tracker" title="SkTorrent Tracker Account">
      {@html ICONS.tracker}
      SkT: {sktUsername}
    </span>
    {#if unreadCount > 0}
      <button 
        class="user-pill mark-read-btn" 
        onclick={onMarkAllRead} 
        title="Mark all new items as read"
      >
        {@html ICONS.check}
        Mark all read
      </button>
    {/if}
  </div>

  <div class="controls">
    <div class="search-wrapper">
      <input 
        type="text" 
        placeholder="Search title..." 
        value={searchQuery}
        oninput={handleInput}
      />
      {#if searchQuery.length > 0}
        <button class="search-clear show" onclick={onClearSearch} title="Clear Search">
          {@html ICONS.close}
        </button>
      {/if}
    </div>
    <button class="theme-toggle" onclick={onToggleTheme} title="Toggle Theme">
      {@html ICONS.theme}
    </button>
  </div>
</header>

<style>
  header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 1.5rem;
    padding-bottom: 1rem;
    border-bottom: 1px solid var(--border-color);
    flex-wrap: wrap;
    gap: 1rem;
  }

  .brand-group {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    flex-wrap: wrap;
  }

  .brand-logo {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    text-decoration: none;
    color: inherit;
  }

  h1 {
    margin: 0;
    font-weight: 800;
    font-size: 1.6rem;
    letter-spacing: -0.03em;
    background: var(--brand-gradient);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }

  .build-badge {
    background: rgba(99, 102, 241, 0.15);
    color: #818cf8;
    border: 1px solid rgba(99, 102, 241, 0.35);
    font-size: 0.725rem;
    font-weight: 600;
    padding: 0.2rem 0.5rem;
    border-radius: 6px;
    letter-spacing: 0.02em;
  }

  .user-status-group {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    flex-wrap: wrap;
  }

  .user-pill {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    background: rgba(var(--surface-rgb), 0.6);
    border: 1px solid var(--border-color);
    padding: 0.25rem 0.65rem;
    border-radius: 9999px;
    font-size: 0.775rem;
    color: var(--text-main);
    font-weight: 500;
    backdrop-filter: blur(8px);
  }

  .user-pill.tracker {
    background: rgba(16, 185, 129, 0.12);
    color: #34d399;
    border-color: rgba(16, 185, 129, 0.3);
  }

  .mark-read-btn {
    cursor: pointer;
    border-color: rgba(99, 102, 241, 0.4);
    transition: var(--transition);
  }

  .mark-read-btn:hover {
    background: rgba(99, 102, 241, 0.2);
  }

  .controls {
    display: flex;
    gap: 0.6rem;
    align-items: center;
    width: 100%;
    max-width: 360px;
  }

  .search-wrapper {
    position: relative;
    flex: 1;
    display: flex;
    align-items: center;
  }

  input[type="text"] {
    width: 100%;
    padding: 0.5rem 2rem 0.5rem 0.85rem;
    font-size: 0.875rem;
    border-radius: 8px;
    border: 1px solid var(--border-color);
    background: rgba(var(--surface-rgb), 0.6);
    backdrop-filter: blur(12px);
    color: var(--text-main);
    outline: none;
    transition: var(--transition);
  }

  input[type="text"]:focus {
    border-color: rgba(99, 102, 241, 0.5);
    box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.15);
  }

  .search-clear {
    position: absolute;
    right: 0.5rem;
    background: none;
    border: none;
    color: var(--text-muted);
    cursor: pointer;
    padding: 0.2rem;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .theme-toggle {
    padding: 0.5rem 0.75rem;
    font-size: 0.875rem;
    border-radius: 8px;
    border: 1px solid var(--border-color);
    background: rgba(var(--surface-rgb), 0.6);
    backdrop-filter: blur(12px);
    color: var(--text-main);
    cursor: pointer;
    outline: none;
    transition: var(--transition);
    display: inline-flex;
    align-items: center;
    justify-content: center;
  }

  .theme-toggle:hover {
    border-color: var(--border-hover);
    background: var(--surface-hover);
  }
</style>
