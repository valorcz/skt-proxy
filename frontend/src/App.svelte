<script>
  import { onMount } from 'svelte';
  import Header from './components/Header.svelte';
  import FilterBar from './components/FilterBar.svelte';
  import NewReleasesBanner from './components/NewReleasesBanner.svelte';
  import TorrentGrid from './components/TorrentGrid.svelte';
  import DetailsModal from './components/DetailsModal.svelte';

  // Config State
  let config = $state({
    app_version: 'v1.0.0',
    current_user: '',
    skt_username: '',
    can_use_nas: false
  });

  // Filter & Search State
  let selectedCategories = $state(JSON.parse(localStorage.getItem('skt_cats') || '[]'));
  let selectedGenres = $state(JSON.parse(localStorage.getItem('skt_genres') || '[]'));
  let categories = $state([]);
  let genres = $state([]);
  let isNewOnly = $state(false);
  let searchQuery = $state('');

  // Feed & Polling State
  let torrents = $state([]);
  let page = $state(0);
  let loading = $state(false);
  let hasMore = $state(true);
  let unreadCount = $state(0);
  let newReleasesCount = $state(0);

  // Modal State
  let activeModalTorrentId = $state(null);

  // Derived visible torrents based on search query
  let visibleTorrents = $derived(
    searchQuery.trim()
      ? torrents.filter(t => t.title?.toLowerCase().includes(searchQuery.toLowerCase().trim()))
      : torrents
  );

  // Theme Handling
  function initTheme() {
    const savedTheme = localStorage.getItem('skt_theme');
    const prefersLight = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches;
    const initialTheme = savedTheme || (prefersLight ? 'light' : 'dark');
    document.documentElement.setAttribute('data-theme', initialTheme);
  }

  function toggleTheme() {
    const currentTheme = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', currentTheme);
    localStorage.setItem('skt_theme', currentTheme);
  }

  // Load Initial Configuration
  async function fetchConfig() {
    try {
      const res = await fetch('/api/config');
      if (res.ok) {
        config = await res.json();
      }
    } catch (e) {
      console.warn('Failed to fetch config, using defaults', e);
    }
  }

  // Load Categories & Genres
  async function fetchFilterOptions() {
    try {
      const [catsRes, genresRes] = await Promise.all([
        fetch('/api/categories'),
        fetch('/api/genres')
      ]);
      if (catsRes.ok) categories = await catsRes.json();
      if (genresRes.ok) genres = await genresRes.json();
    } catch (e) {
      console.error('Failed to load filter options', e);
    }
  }

  // Fetch Torrents
  async function fetchTorrents(reset = false) {
    if (loading) return;
    loading = true;

    if (reset) {
      page = 0;
      torrents = [];
      newReleasesCount = 0;
    }

    const catQuery = selectedCategories.join(',');
    const genreQuery = selectedGenres.join(',');

    try {
      const response = await fetch(
        `/api/torrents?page=${page}&categories=${catQuery}&genres=${genreQuery}&new_only=${isNewOnly}`
      );
      const data = await response.json();

      if (typeof data.unread_count === 'number') {
        unreadCount = data.unread_count;
      }

      const incomingTorrents = data.torrents || [];
      if (reset) {
        torrents = incomingTorrents;
      } else {
        // Append unique torrents
        const existingIds = new Set(torrents.map(t => t.id));
        const newOnes = incomingTorrents.filter(t => !existingIds.has(t.id));
        torrents = [...torrents, ...newOnes];
      }

      hasMore = data.has_more !== false && incomingTorrents.length >= 40;
    } catch (error) {
      console.error('Error loading torrents:', error);
    } finally {
      loading = false;
    }
  }

  function handleLoadMore() {
    page++;
    fetchTorrents(false);
  }

  function handleToggleCategory(catId) {
    if (selectedCategories.includes(catId)) {
      selectedCategories = selectedCategories.filter(id => id !== catId);
    } else {
      selectedCategories = [...selectedCategories, catId];
    }
    localStorage.setItem('skt_cats', JSON.stringify(selectedCategories));
    fetchTorrents(true);
  }

  function handleToggleGenre(genre) {
    if (selectedGenres.includes(genre)) {
      selectedGenres = selectedGenres.filter(g => g !== genre);
    } else {
      selectedGenres = [...selectedGenres, genre];
    }
    localStorage.setItem('skt_genres', JSON.stringify(selectedGenres));
    fetchTorrents(true);
  }

  function handleToggleNewOnly() {
    isNewOnly = !isNewOnly;
    fetchTorrents(true);
  }

  function handleMarkRead(id) {
    const item = torrents.find(t => t.id === id);
    if (item && item.is_new) {
      item.is_new = false;
      unreadCount = Math.max(0, unreadCount - 1);

      fetch('/api/mark_read', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id })
      }).catch(err => console.error('Failed to mark read:', err));
    }
  }

  async function handleMarkAllRead() {
    try {
      const res = await fetch('/api/mark_all_read', { method: 'POST' });
      const data = await res.json();
      if (data.success) {
        torrents = torrents.map(t => ({ ...t, is_new: false }));
        unreadCount = 0;
        if (isNewOnly) {
          fetchTorrents(true);
        }
      }
    } catch (err) {
      console.error('Failed to mark all as read:', err);
    }
  }

  function handleOpenDetails(id) {
    activeModalTorrentId = id;
    handleMarkRead(id);
  }

  function handleCloseDetails() {
    activeModalTorrentId = null;
  }

  onMount(() => {
    initTheme();
    fetchConfig();
    fetchFilterOptions();
    fetchTorrents(true);

    // Background sync polling every 60s
    const pollInterval = setInterval(async () => {
      try {
        const catQuery = selectedCategories.length === 1 ? selectedCategories[0] : '0';
        const res = await fetch(`/api/sync_status?category=${catQuery}`);
        if (res.ok) {
          const data = await res.json();
          if (data.new_items > 0) {
            newReleasesCount = data.new_items;
          }
          if (typeof data.unread_count === 'number') {
            unreadCount = data.unread_count;
          }
        }
      } catch (e) {
        // Silently handle background polling errors
      }
    }, 60000);

    return () => clearInterval(pollInterval);
  });
</script>

<Header 
  appVersion={config.app_version}
  currentUser={config.current_user}
  sktUsername={config.skt_username}
  {unreadCount}
  {searchQuery}
  onSearchChange={(val) => { searchQuery = val; }}
  onClearSearch={() => { searchQuery = ''; }}
  onMarkAllRead={handleMarkAllRead}
  onToggleTheme={toggleTheme}
/>

<FilterBar 
  {categories}
  {selectedCategories}
  onToggleCategory={handleToggleCategory}
  {genres}
  {selectedGenres}
  onToggleGenre={handleToggleGenre}
  {isNewOnly}
  {unreadCount}
  onToggleNewOnly={handleToggleNewOnly}
/>

<NewReleasesBanner 
  count={newReleasesCount}
  onClick={() => fetchTorrents(true)}
/>

<TorrentGrid 
  torrents={visibleTorrents}
  {loading}
  {hasMore}
  canUseNas={config.can_use_nas}
  {isNewOnly}
  onLoadMore={handleLoadMore}
  onOpenDetails={handleOpenDetails}
  onMarkRead={handleMarkRead}
/>

<DetailsModal 
  torrentId={activeModalTorrentId}
  onClose={handleCloseDetails}
  onOpenOtherDetails={handleOpenDetails}
/>
