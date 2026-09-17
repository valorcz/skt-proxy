<script>
  import { ICONS } from '../icons.js';

  let {
    torrent,
    canUseNas = false,
    onOpenDetails = () => {},
    onMarkRead = () => {}
  } = $props();

  let nasConfirming = $state(false);
  let nasState = $state('idle'); // 'idle' | 'confirming' | 'sending' | 'sent' | 'error'
  let nasTimer = null;

  function getScoreClass(scoreStr) {
    if (!scoreStr) return 'neutral';
    const num = parseInt(scoreStr.replace('%', ''), 10);
    if (isNaN(num)) return 'neutral';
    if (num >= 70) return 'good';
    if (num >= 50) return 'avg';
    return 'bad';
  }

  function handleDownloadClick() {
    onMarkRead(torrent.id);
  }

  function handleInfoClick() {
    onOpenDetails(torrent.id);
  }

  async function handleNasClick() {
    if (nasState === 'idle') {
      nasState = 'confirming';
      nasTimer = setTimeout(() => {
        nasState = 'idle';
      }, 3500);
      return;
    }

    if (nasState === 'confirming') {
      clearTimeout(nasTimer);
      nasState = 'sending';

      const encodedTitle = torrent.download_link?.includes('&f=') 
        ? torrent.download_link.split('&f=')[1] 
        : '';

      try {
        const response = await fetch('/api/send_to_nas', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: torrent.id, f: encodedTitle })
        });

        const contentType = response.headers.get('content-type') || '';
        let result;
        if (contentType.includes('application/json')) {
          result = await response.json();
        } else {
          const text = await response.text();
          throw new Error(`Server error (${response.status}): ${text.slice(0, 100)}`);
        }

        if (response.ok && result.success) {
          nasState = 'sent';
          onMarkRead(torrent.id);
        } else {
          alert('Failed: ' + (result.error || result.message || `HTTP ${response.status}`));
          nasState = 'error';
        }
      } catch (err) {
        alert(err.message || ('Network error: ' + err));
        nasState = 'idle';
      }

      setTimeout(() => {
        nasState = 'idle';
      }, 3000);
    }
  }
</script>

<article class="card {torrent.is_new ? 'is-new' : ''}" data-id={torrent.id}>
  <div class="card-img-wrapper">
    {#if torrent.csfd_score}
      <a 
        href={torrent.csfd_id ? `https://www.csfd.cz/film/${torrent.csfd_id}` : '#'} 
        target={torrent.csfd_id ? '_blank' : undefined} 
        rel="noopener noreferrer"
        class="csfd-overlay-badge {getScoreClass(torrent.csfd_score)}" 
        title="ČSFD Score: {torrent.csfd_score}"
      >
        {@html ICONS.star} {torrent.csfd_score}
      </a>
    {/if}
    <img 
      class="card-img" 
      src={torrent.display_image} 
      alt={torrent.title} 
      loading="lazy" 
      onerror={(e) => { e.currentTarget.style.display = 'none'; }}
    />
  </div>

  <div class="card-content">
    <h2 class="title" title={torrent.title}>{torrent.title}</h2>

    <div class="meta">
      {#if torrent.is_new}
        <span class="badge new">New</span>
      {/if}
      <span class="badge">{torrent.category}</span>
      {#if torrent.genres}
        {#each torrent.genres as g}
          <span class="badge">{g}</span>
        {/each}
      {/if}
      {#if torrent.languages}
        {#each torrent.languages as lang}
          <span class="badge lang">{lang}</span>
        {/each}
      {/if}
      <span class="badge">{torrent.size}</span>
      <span class="badge">{torrent.added_date}</span>
    </div>

    <div class="stats">
      <span class="seed">{@html ICONS.seed} {torrent.seeders}</span>
      <span class="leech">{@html ICONS.leech} {torrent.leechers}</span>
    </div>

    <div class="action-stack">
      <div class="action-grid">
        <!-- Slot 1: Download -->
        <a 
          href={torrent.download_link} 
          onclick={handleDownloadClick} 
          class="btn"
        >
          {@html ICONS.download} Download
        </a>

        <!-- Slot 2: Info -->
        <button 
          class="btn btn-secondary" 
          onclick={handleInfoClick}
        >
          {@html ICONS.info} Info
        </button>

        <!-- Slot 3: Book / CSFD Link -->
        {#if torrent.content_type === 'book'}
          <a 
            href="https://www.databazeknih.cz/search?q={encodeURIComponent(torrent.title)}" 
            target="_blank" 
            rel="noopener noreferrer"
            class="btn btn-book" 
            title="Search on Databáze knih"
          >
            {@html ICONS.book} Knihy
          </a>
        {:else if torrent.csfd_id}
          <a 
            href="https://www.csfd.cz/film/{torrent.csfd_id}" 
            target="_blank" 
            rel="noopener noreferrer"
            class="btn btn-csfd" 
            title="Open on ČSFD.cz"
          >
            {@html ICONS.csfd} {torrent.csfd_score || 'ČSFD'}
          </a>
        {:else}
          <button class="btn btn-secondary" disabled title="No rating link available">
            {@html ICONS.csfd} N/A
          </button>
        {/if}

        <!-- Slot 4: NAS Push with 2-stage confirmation -->
        {#if canUseNas}
          <button 
            onclick={handleNasClick} 
            class="btn btn-secondary {nasState === 'confirming' ? 'btn-nas-confirm' : ''}"
            disabled={nasState === 'sending'}
            style={nasState === 'sent' ? 'border-color: var(--seed-color); color: var(--seed-color);' : ''}
          >
            {#if nasState === 'idle'}
              {@html ICONS.nas} NAS
            {:else if nasState === 'confirming'}
              {@html ICONS.nas} Confirm NAS?
            {:else if nasState === 'sending'}
              Sending...
            {:else if nasState === 'sent'}
              Sent
            {:else if nasState === 'error'}
              Error
            {/if}
          </button>
        {:else}
          <button class="btn btn-secondary" disabled title="NAS authorization required">
            {@html ICONS.nas} NAS
          </button>
        {/if}
      </div>
    </div>
  </div>
</article>

<style>
  .card {
    background-color: rgba(var(--surface-rgb), 0.5);
    backdrop-filter: blur(12px);
    border-radius: var(--radius);
    border: 1px solid var(--border-color);
    overflow: hidden;
    display: flex;
    flex-direction: column;
    transition: var(--transition);
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  }

  .card:hover {
    border-color: rgba(99, 102, 241, 0.4);
    background-color: var(--surface-hover);
    transform: translateY(-4px);
    box-shadow: 0 14px 28px -5px rgba(0, 0, 0, 0.25);
  }

  .card.is-new {
    border-color: rgba(99, 102, 241, 0.45);
    box-shadow: 0 4px 20px rgba(99, 102, 241, 0.16);
  }

  .card-img-wrapper {
    position: relative;
    width: 100%;
    height: 320px;
    overflow: hidden;
    border-bottom: 1px solid var(--border-color);
    background: rgba(0, 0, 0, 0.2);
  }

  .card-img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    transition: transform 0.4s ease, opacity 0.3s ease;
  }

  .card:hover .card-img {
    transform: scale(1.05);
    opacity: 0.95;
  }

  .csfd-overlay-badge {
    position: absolute;
    top: 10px;
    right: 10px;
    z-index: 2;
    padding: 0.25rem 0.55rem;
    border-radius: 6px;
    font-size: 0.775rem;
    font-weight: 700;
    display: flex;
    align-items: center;
    gap: 0.3rem;
    backdrop-filter: blur(8px);
    box-shadow: 0 4px 10px rgba(0, 0, 0, 0.3);
    text-decoration: none;
  }
  .csfd-overlay-badge.good { background: rgba(16, 185, 129, 0.9); color: #ffffff; }
  .csfd-overlay-badge.avg { background: rgba(245, 158, 11, 0.9); color: #ffffff; }
  .csfd-overlay-badge.bad { background: rgba(239, 68, 68, 0.9); color: #ffffff; }
  .csfd-overlay-badge.neutral { background: rgba(100, 116, 139, 0.85); color: #ffffff; }
  .csfd-overlay-badge:hover { transform: scale(1.05); }

  .card-content {
    padding: 1rem;
    display: flex;
    flex-direction: column;
    flex-grow: 1;
  }

  .title {
    font-size: 0.975rem;
    font-weight: 600;
    margin: 0 0 0.5rem 0;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    line-height: 1.4;
    letter-spacing: -0.01em;
    word-break: break-word;
  }

  .meta {
    font-size: 0.725rem;
    color: var(--text-muted);
    margin-bottom: 0.75rem;
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem;
    align-items: center;
  }

  .badge {
    background: rgba(var(--surface-rgb), 0.6);
    border: 1px solid var(--border-color);
    padding: 0.15rem 0.5rem;
    border-radius: 6px;
    backdrop-filter: blur(4px);
    text-decoration: none;
    color: inherit;
  }
  .badge.new {
    background: var(--brand-gradient);
    color: var(--primary-invert);
    border: none;
    font-weight: 600;
  }
  .badge.lang {
    background: rgba(59, 130, 246, 0.15);
    color: #60a5fa;
    border: 1px solid rgba(59, 130, 246, 0.3);
    font-weight: 600;
  }

  .stats {
    display: flex;
    gap: 0.85rem;
    font-size: 0.775rem;
    margin-bottom: 0.75rem;
    margin-top: auto;
    padding-top: 0.65rem;
    font-weight: 600;
    border-top: 1px dashed var(--border-color);
  }
  .seed { color: var(--seed-color); display: inline-flex; align-items: center; gap: 0.2rem; }
  .leech { color: var(--leech-color); display: inline-flex; align-items: center; gap: 0.2rem; }

  .action-stack {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
    margin-top: 0.35rem;
  }

  .action-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    grid-template-rows: 1fr 1fr;
    gap: 0.35rem;
  }
</style>
