<script>
  let { count = 0, onClick = () => {} } = $props();
</script>

{#if count > 0}
  <button class="new-releases-pill" onclick={onClick}>
    ✦ {count} new release{count > 1 ? 's' : ''} available on tracker — Click to refresh
  </button>
{/if}

<style>
  .new-releases-pill {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
    background: var(--brand-gradient);
    color: #ffffff;
    padding: 0.5rem 1.25rem;
    border-radius: 9999px;
    margin: 0 auto 1.5rem auto;
    cursor: pointer;
    font-weight: 600;
    font-size: 0.85rem;
    box-shadow: 0 8px 25px rgba(99, 102, 241, 0.35);
    transition: var(--transition);
    max-width: fit-content;
    border: none;
  }

  .new-releases-pill:hover {
    transform: translateY(-2px) scale(1.03);
  }
</style>
