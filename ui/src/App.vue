<script setup lang="ts">
import { defineAsyncComponent } from 'vue'
const LegacyStudio = defineAsyncComponent(() => import('./LegacyStudio.vue'))
import WorkbenchShell from './components/workbench/WorkbenchShell.vue'
const query = new URLSearchParams(window.location.search)
const legacy =
  query.get('view') === 'legacy' || ['mock', 'scenario', 'drawer', 'gate'].some((key) => query.has(key))
</script>
<template><LegacyStudio v-if="legacy" /><WorkbenchShell v-else /></template>
