<script>
  let {
    categories = [],
    selectedCategories = [],
    onToggleCategory = () => {},
    genres = [],
    selectedGenres = [],
    onToggleGenre = () => {},
    isNewOnly = false,
    unreadCount = 0,
    onToggleNewOnly = () => {}
  } = $props();

  let dropdownOpen = $state(false);
  let dropdownRef;

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
      onclick={onToggleNewOnly}
    >
      ⚡ New Only ({unreadCount})
    </button>
  </div>

  <div class="filter-row">
    <span class="filter-label">Categories:</span>
    {#each categories as cat (cat.id)}
      <button 
        class="pill {selectedCategories.includes(cat.id) ? 'active' : ''}"
        onclick={() => onToggleCategory(cat.id)}
      >
        {cat.name}
      </button>
    {/each}
  </div>

  <div class="filter-row">
    <span class="filter-label">Genres:</span>
    <div class="dropdown" bind:this={dropdownRef}>
      <button class="pill" onclick={toggleDropdown}>
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
