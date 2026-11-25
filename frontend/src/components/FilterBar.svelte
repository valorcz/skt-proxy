<script>
  import { ICONS } from '../icons.js';

  let {
    categories = [],
    selectedCategories = [],
    onToggleCategory = () => {},
    genres = [],
    selectedGenres = [],
    onToggleGenre = () => {},
    isNewOnly = false,
    unreadCount = 0,
    lastSynced = 0,
    syncing = false,
    onToggleNewOnly = () => {},
    onMarkAllRead = () => {},
    onSync = () => {}
  } = $props();

  let dropdownOpen = $state(false);
  let dropdownRef;

  function formatLastSynced(ts) {
    if (!ts) return 'Never';
    const diffSec = Math.max(0, Math.floor(Date.now() / 1000 - ts));
    if (diffSec < 60) return 'just now';
    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHr = Math.floor(diffMin / 60);
    return `${diffHr}h ago`;
  }

  function toggleDropdown(e) {
    e.stopPropagation();
    dropdownOpen = !dropdownOpen;
  }

  function handleWindowClick(e) {
    if (dropdownOpen && dropdownRef && !dropdownRef.contains(e.target)) {
      dropdownOpen = false;
    }
  }
</script>

<svelte:window onclick={handleWindowClick} />

<div class="filters-container">
  <div class="filter-row">
    <span class="filter-label">Feed:</span>
    <button 
      class="pill {isNewOnly ? 'active' : ''}" 
      aria-pressed={isNewOnly}
      onclick={onToggleNewOnly}
    >
      ⚡ New Only ({unreadCount})
    </button>
    {#if unreadCount > 0}
      <button 
        class="pill mark-read-btn" 
        onclick={onMarkAllRead} 
        title="Mark all new items as read"
      >
        {@html ICONS.check}
        Mark all read
      </button>
    {/if}
    <button
      class="pill sync-btn"
      onclick={onSync}
      disabled={syncing}
      title="Sync latest torrents from tracker"
    >
      {syncing ? 'Syncing...' : '↻ Sync'}
    </button>
    {#if lastSynced > 0}
      <span class="sync-meta">Synced {formatLastSynced(lastSynced)}</span>
    {/if}
  </div>

  <div class="filter-row">
    <span class="filter-label">Categories:</span>
    {#each categories as cat (cat.id)}
      <button 
        class="pill {selectedCategories.includes(cat.id) ? 'active' : ''}"
        aria-pressed={selectedCategories.includes(cat.id)}
        onclick={() => onToggleCategory(cat.id)}
      >
        {cat.name}
      </button>
    {/each}
  </div>

  <div class="filter-row">
    <span class="filter-label">Genres:</span>
    <div class="dropdown" bind:this={dropdownRef}>
      <button class="pill" aria-expanded={dropdownOpen} onclick={toggleDropdown}>
        {selectedGenres.length > 0 ? `Genres (${selectedGenres.length}) ▾` : 'Select Genres ▾'}
      </button>
      <div class="dropdown-content {dropdownOpen ? 'show' : ''}">
        {#each genres as g (g)}
          <label class="dropdown-label">
            <input 
              type="checkbox" 
              checked={selectedGenres.includes(g)}
              onchange={() => onToggleGenre(g)}
            />
            {g}
          </label>
        {/each}
      </div>
    </div>
    <div class="active-genres-container">
      {#each selectedGenres as g (g)}
        <button class="pill active" onclick={() => onToggleGenre(g)}>
          {g} ✕
        </button>
      {/each}
    </div>
  </div>
</div>

<style>
  .filters-container {
    display: flex;
    flex-direction: column;
    gap: 0.85rem;
    margin-bottom: 2rem;
  }

  .filter-row {
    display: flex;
    flex-wrap: wrap;
    gap: 0.45rem;
    align-items: center;
  }

  .filter-label {
    font-size: 0.825rem;
    color: var(--text-muted);
    margin-right: 0.4rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  .pill {
    background: rgba(var(--surface-rgb), 0.6);
    backdrop-filter: blur(8px);
    border: 1px solid var(--border-color);
    color: var(--text-main);
    padding: 0.35rem 0.75rem;
    border-radius: 9999px;
    cursor: pointer;
    font-size: 0.825rem;
    transition: var(--transition);
    user-select: none;
  }

  .pill:hover {
    border-color: var(--border-hover);
    transform: translateY(-1px);
  }

  .pill.active {
    background: var(--brand-gradient);
    color: var(--primary-invert);
    border: none;
    font-weight: 600;
    box-shadow: 0 4px 12px rgba(99, 102, 241, 0.25);
  }

  .mark-read-btn {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    border-color: rgba(99, 102, 241, 0.4);
  }

  .mark-read-btn:hover {
    background: rgba(99, 102, 241, 0.2);
    border-color: rgba(99, 102, 241, 0.65);
  }

  .sync-btn:disabled {
    opacity: 0.6;
    cursor: wait;
  }

  .sync-meta {
    font-size: 0.775rem;
    color: var(--text-muted);
    margin-left: 0.2rem;
  }

  .dropdown {
    position: relative;
    display: inline-block;
  }

  .dropdown-content {
    display: none;
    position: absolute;
    background: rgba(var(--surface-rgb), 0.95);
    backdrop-filter: blur(20px);
    min-width: 220px;
    max-height: 340px;
    overflow-y: auto;
    border: 1px solid var(--border-color);
    border-radius: var(--radius);
    box-shadow: 0 12px 30px -5px rgba(0, 0, 0, 0.4);
    z-index: 20;
    padding: 0.75rem;
    top: 115%;
    left: 0;
    flex-direction: column;
    gap: 0.45rem;
  }

  .dropdown-content.show {
    display: flex;
  }

  .dropdown-label {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    font-size: 0.825rem;
    cursor: pointer;
    color: var(--text-main);
  }

  .active-genres-container {
    display: flex;
    gap: 0.4rem;
    flex-wrap: wrap;
    margin-left: 0.4rem;
  }
</style>
