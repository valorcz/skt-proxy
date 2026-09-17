<script>
  import TorrentCard from './TorrentCard.svelte';

  let {
    torrents = [],
    loading = false,
    hasMore = false,
    canUseNas = false,
    isNewOnly = false,
    onLoadMore = () => {},
    onOpenDetails = () => {},
    onMarkRead = () => {}
  } = $props();
</script>

<main class="grid">
  {#if torrents.length === 0 && loading}
    <div class="card"><div class="skeleton"></div></div>
    <div class="card"><div class="skeleton"></div></div>
    <div class="card"><div class="skeleton"></div></div>
  {:else if torrents.length === 0}
    <p class="empty-state">
      {isNewOnly ? 'No new unread torrents found.' : 'No results found for these filters.'}
    </p>
  {:else}
    {#each torrents as torrent (torrent.id)}
      <TorrentCard 
        {torrent}
        {canUseNas}
        {onOpenDetails}
        {onMarkRead}
      />
    {/each}
  {/if}
</main>

<div class="load-more-container">
  {#if torrents.length > 0}
    <button 
      class="btn btn-secondary load-more-btn" 
      onclick={onLoadMore}
      disabled={loading || !hasMore}
    >
      {#if loading}
        Loading...
      {:else if hasMore}
        Load More
      {:else}
        No more items
      {/if}
    </button>
  {/if}
</div>

<style>
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
    gap: 1.25rem;
    margin-bottom: 3rem;
  }

  .empty-state {
    color: var(--text-muted);
    grid-column: 1 / -1;
    text-align: center;
    padding: 3rem 1rem;
    font-size: 1rem;
  }

  .load-more-container {
    text-align: center;
    max-width: 200px;
    margin: 0 auto 3rem auto;
  }

  .load-more-btn {
    width: 100%;
  }

  .card {
    background-color: rgba(var(--surface-rgb), 0.5);
    backdrop-filter: blur(12px);
    border-radius: var(--radius);
    border: 1px solid var(--border-color);
    overflow: hidden;
  }
</style>
