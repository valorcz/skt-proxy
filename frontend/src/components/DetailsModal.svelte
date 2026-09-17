<script>
  import { ICONS } from '../icons.js';

  let {
    torrentId = null,
    onClose = () => {},
    onOpenOtherDetails = () => {}
  } = $props();

  let details = $state(null);
  let loading = $state(true);
  let error = $state(null);

  $effect(() => {
    if (torrentId) {
      loadDetails(torrentId);
    }
  });

  async function loadDetails(id) {
    loading = true;
    error = null;
    details = null;

    try {
      const res = await fetch(`/api/torrent_details?id=${id}`);
      const data = await res.json();
      if (!res.ok || data.error) {
        error = data.error || 'Unknown error';
      } else {
        details = data;
      }
    } catch (err) {
      error = err.message || 'Network error';
    } finally {
      loading = false;
    }
  }

  function getScoreClass(scoreStr) {
    if (!scoreStr) return 'neutral';
    const num = parseInt(scoreStr.replace('%', ''), 10);
    if (isNaN(num)) return 'neutral';
    if (num >= 70) return 'good';
    if (num >= 50) return 'avg';
    return 'bad';
  }

  function handleOverlayClick(e) {
    if (e.target === e.currentTarget) {
      onClose();
    }
  }

  function handleKeydown(e) {
    if (e.key === 'Escape') {
      onClose();
    }
  }
</script>

<svelte:window onkeydown={handleKeydown} />

{#if torrentId}
  <div class="modal-overlay" onclick={handleOverlayClick} role="presentation">
    <div class="modal-content" role="dialog" aria-modal="true">
      <div class="modal-header">
        <h2>{details?.title || (loading ? 'Loading Torrent Details...' : 'Torrent Details')}</h2>
        <button class="modal-close" onclick={onClose} title="Close">
          {@html ICONS.close}
        </button>
      </div>

      <div class="modal-body">
        {#if loading}
          <p class="loading-text">Fetching description and MediaInfo...</p>
        {:else if error}
          <p class="error-text">Error loading details: {error}</p>
        {:else if details}
          {#if details.poster_url}
            <img 
              class="detail-poster" 
              src={details.poster_url} 
              alt="Poster" 
              loading="lazy"
              onerror={(e) => { e.currentTarget.style.display = 'none'; }}
            />
          {/if}

          <div class="modal-meta">
            <!-- Content Type -->
            <span class="badge type-badge">
              {#if details.content_type === 'tv'}
                📺 Series {details.season_episode ? `(${details.season_episode})` : ''}
              {:else if details.content_type === 'book'}
                {#if details.category && details.category.toLowerCase().includes('slovo')}
                  🗣️ Spoken Word / Audiobook
                {:else}
                  📚 Book / Audiobook
                {/if}
              {:else if details.content_type === 'music'}
                🎵 Music
              {:else if details.content_type === 'software'}
                💻 Software / Game
              {:else}
                🎬 Movie
              {/if}
            </span>

            <!-- CSFD Score -->
            {#if details.csfd_score}
              <span class="badge csfd {getScoreClass(details.csfd_score)}">
                {@html ICONS.star} Score: {details.csfd_score}
              </span>
            {/if}

            <!-- Quality & Codec Badges -->
            {#if details.quality_tags}
              {#each details.quality_tags as q}
                <span class="badge quality-badge">{q}</span>
              {/each}
            {/if}

            <!-- External Links: Tracker + Review Sites -->
            {#if details.sktorrent_url}
              <a href={details.sktorrent_url} target="_blank" rel="noopener noreferrer" class="badge skt">
                {@html ICONS.globe} Open on SkTorrent.eu
              </a>
            {/if}

            {#if details.csfd_url}
              <a href={details.csfd_url} target="_blank" rel="noopener noreferrer" class="badge csfd-link">
                {@html ICONS.csfd} Open on ČSFD.cz
              </a>
            {/if}

            {#if details.databazeknih_url}
              <a href={details.databazeknih_url} target="_blank" rel="noopener noreferrer" class="badge book-link">
                {@html ICONS.book} Open on Databáze knih
              </a>
            {/if}

            {#if details.imdb_url}
              <a href={details.imdb_url} target="_blank" rel="noopener noreferrer" class="badge imdb-link">
                Open on IMDb
              </a>
            {/if}

            <!-- Languages -->
            {#if details.languages}
              {#each details.languages as l}
                <span class="badge lang">Audio/Sub: {l}</span>
              {/each}
            {/if}

            <!-- File Size -->
            {#if details.size}
              <span class="badge">Size: {details.size}</span>
            {/if}
          </div>

          <!-- Clean Synopsis -->
          {#if details.synopsis && details.synopsis.trim().length > 0}
            <div class="section-header">Synopsis / Obsah:</div>
            <div class="synopsis-box">{details.synopsis}</div>
          {/if}

          <!-- MediaInfo & Codec Specs (Restored to original open format) -->
          {#if details.mediainfo_text && details.mediainfo_text.trim().length > 0}
            <div class="section-header">
              {#if details.content_type === 'music'}
                Tracklist & Audio Specs:
              {:else if details.content_type === 'book'}
                Book & File Details:
              {:else}
                MediaInfo & Codec Specs:
              {/if}
            </div>
            <div class="mediainfo-box">
              <pre>{details.mediainfo_text}</pre>
            </div>
          {/if}

          <!-- Trailer -->
          {#if details.trailer_url}
            <div class="section-header">Trailer:</div>
            <div class="trailer-container">
              <iframe 
                src={details.trailer_url} 
                title="Trailer" 
                allowfullscreen
              ></iframe>
            </div>
          {/if}

          <!-- Related Torrents with direct actions -->
          {#if details.related_torrents && details.related_torrents.length > 0}
            <div class="section-header">Other Versions & Copies on SkTorrent ({details.related_torrents.length}):</div>
            <div class="related-box">
              {#each details.related_torrents as r}
                <div class="related-item">
                  <span class="related-item-title" title={r.title}>{r.title}</span>
                  <div class="related-actions">
                    <button 
                      class="btn btn-secondary small-btn" 
                      onclick={() => onOpenOtherDetails(r.id)}
                      title="View Details"
                    >
                      {@html ICONS.info} Info
                    </button>
                    {#if r.download_link}
                      <a 
                        href={r.download_link} 
                        class="btn small-btn" 
                        title="Download Torrent"
                      >
                        {@html ICONS.download}
                      </a>
                    {/if}
                    {#if r.sktorrent_url}
                      <a 
                        href={r.sktorrent_url} 
                        target="_blank" 
                        rel="noopener noreferrer" 
                        class="btn btn-secondary small-btn"
                        title="Open on SkTorrent.eu"
                      >
                        {@html ICONS.globe} SkT
                      </a>
                    {/if}
                  </div>
                </div>
              {/each}
            </div>
          {/if}
        {/if}
      </div>
    </div>
  </div>
{/if}

<style>
  .modal-overlay {
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0, 0, 0, 0.8);
    backdrop-filter: blur(10px);
    z-index: 100;
    display: flex;
    justify-content: center;
    align-items: center;
    padding: 1rem;
  }

  .modal-content {
    background: rgba(var(--surface-rgb), 0.95);
    border: 1px solid var(--border-color);
    border-radius: var(--radius);
    width: 100%;
    max-width: 680px;
    max-height: 88vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
    animation: modalIn 0.25s ease-out;
  }

  @keyframes modalIn {
    from { opacity: 0; transform: translateY(15px); }
    to { opacity: 1; transform: translateY(0); }
  }

  .modal-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.9rem 1.25rem;
    border-bottom: 1px solid var(--border-color);
    background: rgba(var(--surface-rgb), 0.8);
  }

  .modal-header h2 {
    margin: 0;
    font-size: 1.05rem;
    font-weight: 700;
    line-height: 1.3;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 85%;
  }

  .modal-close {
    background: none;
    border: none;
    font-size: 1.2rem;
    color: var(--text-muted);
    cursor: pointer;
    padding: 0.2rem;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .modal-close:hover { color: var(--text-main); }

  .modal-body {
    padding: 1.25rem;
    overflow-y: auto;
    font-size: 0.9rem;
    line-height: 1.6;
  }

  .loading-text {
    color: var(--text-muted);
    text-align: center;
    padding: 2rem 0;
  }

  .error-text {
    color: var(--leech-color);
    text-align: center;
    padding: 1.5rem 0;
  }

  .detail-poster {
    width: 100%;
    max-height: 350px;
    object-fit: contain;
    border-radius: 8px;
    margin-bottom: 1.15rem;
    background: rgba(0, 0, 0, 0.3);
    border: 1px solid var(--border-color);
  }

  .modal-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 0.45rem;
    margin-bottom: 1.15rem;
  }

  .badge {
    background: rgba(var(--surface-rgb), 0.6);
    border: 1px solid var(--border-color);
    padding: 0.15rem 0.5rem;
    border-radius: 6px;
    backdrop-filter: blur(4px);
    text-decoration: none;
    color: inherit;
    font-size: 0.775rem;
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
  }

  .type-badge {
    background: rgba(99, 102, 241, 0.2);
    color: #a5b4fc;
    border-color: rgba(99, 102, 241, 0.4);
    font-weight: 600;
  }

  .quality-badge {
    background: rgba(56, 189, 248, 0.15);
    color: #38bdf8;
    border-color: rgba(56, 189, 248, 0.35);
    font-weight: 600;
  }

  .badge.skt {
    background: rgba(139, 92, 246, 0.15);
    color: #c084fc;
    border-color: rgba(139, 92, 246, 0.35);
    font-weight: 600;
  }

  .badge.csfd-link {
    background: rgba(225, 29, 72, 0.15);
    color: #f43f5e;
    border-color: rgba(225, 29, 72, 0.35);
    font-weight: 600;
  }

  .badge.book-link {
    background: rgba(16, 185, 129, 0.2);
    color: #34d399;
    border-color: rgba(16, 185, 129, 0.4);
    font-weight: 600;
  }

  .badge.imdb-link {
    background: rgba(245, 158, 11, 0.2);
    color: #fbbf24;
    border-color: rgba(245, 158, 11, 0.4);
    font-weight: 600;
  }

  .badge.lang {
    background: rgba(59, 130, 246, 0.15);
    color: #60a5fa;
    border-color: rgba(59, 130, 246, 0.3);
    font-weight: 600;
  }

  .section-header {
    font-weight: 600;
    font-size: 0.9rem;
    margin-bottom: 0.45rem;
    color: var(--text-main);
  }

  .synopsis-box {
    background: rgba(var(--surface-rgb), 0.5);
    border: 1px solid var(--border-color);
    border-radius: 8px;
    padding: 0.9rem 1rem;
    margin-bottom: 1.15rem;
    white-space: pre-wrap;
    font-size: 0.925rem;
    color: var(--text-main);
    line-height: 1.6;
  }

  .trailer-container {
    margin-bottom: 1.15rem;
    position: relative;
    padding-bottom: 56.25%;
    height: 0;
    overflow: hidden;
    border-radius: 8px;
    border: 1px solid var(--border-color);
  }
  .trailer-container iframe {
    position: absolute;
    top: 0; left: 0; width: 100%; height: 100%;
    border: 0;
  }

  .related-box {
    background: rgba(var(--surface-rgb), 0.4);
    border: 1px solid var(--border-color);
    border-radius: 8px;
    padding: 0.75rem 0.85rem;
    margin-bottom: 1.15rem;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }

  .related-item {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.4rem 0.6rem;
    border-radius: 6px;
    background: rgba(0, 0, 0, 0.2);
    border: 1px solid var(--border-color);
    gap: 0.5rem;
    font-size: 0.825rem;
  }
  .related-item-title { flex: 1; word-break: break-word; font-weight: 500; }
  .related-actions { display: flex; gap: 0.3rem; flex-shrink: 0; }

  .small-btn {
    padding: 0.2rem 0.45rem !important;
    font-size: 0.75rem !important;
    width: auto !important;
  }

  .mediainfo-box {
    background: rgba(0, 0, 0, 0.45);
    border: 1px solid var(--border-color);
    border-radius: 8px;
    padding: 0.85rem;
    margin-bottom: 1.15rem;
    overflow-x: auto;
    max-height: 250px;
    overflow-y: auto;
  }

  .mediainfo-box pre {
    margin: 0;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    font-size: 0.8rem;
    color: #38bdf8;
    line-height: 1.45;
  }
</style>
