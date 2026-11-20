<script setup lang="ts">
import LegacyStudio from './LegacyStudio.vue'
import WorkbenchShell from './components/workbench/WorkbenchShell.vue'
const query = new URLSearchParams(window.location.search)
const legacy =
  query.get('view') === 'legacy' || ['mock', 'scenario', 'drawer', 'gate'].some((key) => query.has(key))
</script>
<template><LegacyStudio v-if="legacy" /><WorkbenchShell v-else /></template>
