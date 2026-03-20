<template>
  <div class="graph-canvas" ref="containerRef">
    <svg ref="svgRef" :width="width" :height="height">
      <g ref="gRef">
        <!-- edges rendered first (below nodes) -->
        <line
          v-for="edge in renderedEdges"
          :key="edge.key"
          :x1="edge.x1" :y1="edge.y1" :x2="edge.x2" :y2="edge.y2"
          :class="['graph-edge', `graph-edge--${edge.type}`]"
          :stroke-dasharray="edgeDash(edge.type)"
        />

        <!-- memory nodes (circles) — use allMemoryNodes for simulation positions -->
        <g
          v-for="node in allMemoryNodes"
          :key="node.id"
          :class="['graph-node', store.selectedMemoryId && node.id === 'mem_' + store.selectedMemoryId ? 'graph-node--selected' : '']"
          :transform="`translate(${node.x || 0},${node.y || 0})`"
          @click="emit('node-select', node.id)"
          @mouseenter="showTooltip($event, node)"
          @mouseleave="hideTooltip"
          style="cursor:pointer"
        >
          <circle
            :r="nodeRadius(node)"
            :fill="memoryColor(node.outcome)"
            stroke="#333"
            stroke-width="1.5"
          />
          <text
            y="3"
            text-anchor="middle"
            class="node-label"
          >{{ nodeLabel(node) }}</text>
        </g>

        <!-- experiment nodes (rects) — use allExpNodes for simulation positions -->
        <g
          v-for="node in allExpNodes"
          :key="node.id"
          :transform="`translate(${(node.x || 0) - 8},${(node.y || 0) - 8})`"
          @click="emit('node-select', node.id)"
          @mouseenter="showTooltip($event, node)"
          @mouseleave="hideTooltip"
          style="cursor:pointer"
        >
          <rect
            width="16" height="16"
            :fill="expColor(node.status)"
            stroke="#333"
            stroke-width="1.5"
          />
        </g>
      </g>
    </svg>

    <!-- Tooltip -->
    <div
      v-if="tooltip.visible"
      class="graph-tooltip"
      :style="{ left: tooltip.x + 'px', top: tooltip.y + 'px' }"
    >
      <div class="tooltip-label">{{ tooltip.label }}</div>
      <div v-if="tooltip.theory" class="tooltip-theory">{{ tooltip.theory }}</div>
      <div v-if="tooltip.sharpe != null" class="tooltip-metric">
        SHARPE: {{ typeof tooltip.sharpe === 'number' ? tooltip.sharpe.toFixed(3) : tooltip.sharpe }}
      </div>
      <div v-if="tooltip.outcome" class="tooltip-outcome" :class="`tooltip-outcome--${tooltip.outcome.toLowerCase()}`">
        {{ tooltip.outcome }}
      </div>
    </div>

    <div v-if="!props.nodes.length" class="empty-graph">
      <span>NO GRAPH DATA</span>
      <span class="empty-sub">RUN A RESEARCH LOOP TO BUILD THE KNOWLEDGE GRAPH</span>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import * as d3 from 'd3'
import { useMemoryStore } from '@/stores/useMemoryStore.js'

const props = defineProps({
  nodes: { type: Array, default: () => [] },
  edges: { type: Array, default: () => [] },
})

const emit = defineEmits(['node-select'])
const store = useMemoryStore()

const containerRef = ref(null)
const svgRef = ref(null)
const gRef = ref(null)
const width = ref(800)
const height = ref(600)

const tooltip = ref({ visible: false, x: 0, y: 0, label: '', theory: '', sharpe: null, outcome: '' })

// Simulation state — updated on every tick to drive reactivity
let simulation = null
const nodePositions = ref({})
const renderedEdges = ref([])

// Derived node lists with positions injected from simulation ticks
const allNodes = computed(() => {
  const pos = nodePositions.value
  return props.nodes.map(n => ({
    ...n,
    x: pos[n.id]?.x ?? n.x ?? width.value / 2,
    y: pos[n.id]?.y ?? n.y ?? height.value / 2,
  }))
})

const allMemoryNodes = computed(() => allNodes.value.filter(n => n.type === 'memory'))
const allExpNodes = computed(() => allNodes.value.filter(n => n.type === 'experiment'))

function nodeRadius(node) {
  if (node.sharpe == null) return 10
  return Math.max(8, Math.min(22, 10 + Math.abs(node.sharpe) * 8))
}

function nodeLabel(node) {
  const label = node.label || ''
  return label.length > 12 ? label.slice(0, 12) : label
}

function memoryColor(outcome) {
  if (outcome === 'POSITIVE') return '#4caf50'
  if (outcome === 'NEGATIVE') return '#f44336'
  return '#ffeb3b'
}

function expColor(status) {
  if (status === 'succeeded') return '#4caf50'
  if (status === 'failed') return '#f44336'
  if (status === 'running') return '#2196f3'
  return '#9e9e9e'
}

function edgeDash(type) {
  if (type === 'same_tags') return '4,4'
  if (type === 'same_session') return '2,3'
  return 'none'
}

function showTooltip(event, node) {
  const rect = containerRef.value?.getBoundingClientRect() || { left: 0, top: 0 }
  tooltip.value = {
    visible: true,
    x: event.clientX - rect.left + 12,
    y: event.clientY - rect.top - 10,
    label: node.label || node.id,
    theory: node.theory ? node.theory.slice(0, 120) : '',
    sharpe: node.sharpe,
    outcome: node.outcome || node.status || '',
  }
}

function hideTooltip() {
  tooltip.value.visible = false
}

function runSimulation() {
  if (!props.nodes.length) return

  const nodesCopy = props.nodes.map(n => ({ ...n }))
  const nodeMap = Object.fromEntries(nodesCopy.map(n => [n.id, n]))

  const linksCopy = props.edges
    .filter(e => nodeMap[e.source] && nodeMap[e.target])
    .map(e => ({ ...e, source: e.source, target: e.target }))

  simulation?.stop()
  simulation = d3.forceSimulation(nodesCopy)
    .force('link', d3.forceLink(linksCopy).id(d => d.id).distance(80))
    .force('charge', d3.forceManyBody().strength(-200))
    .force('center', d3.forceCenter(width.value / 2, height.value / 2))
    .force('collide', d3.forceCollide(20))
    .on('tick', () => {
      // Update rendered edges using live mutated objects from d3
      renderedEdges.value = linksCopy.map((e, i) => {
        const src = typeof e.source === 'object' ? e.source : nodeMap[e.source]
        const tgt = typeof e.target === 'object' ? e.target : nodeMap[e.target]
        return {
          key: i,
          x1: src?.x || 0,
          y1: src?.y || 0,
          x2: tgt?.x || 0,
          y2: tgt?.y || 0,
          type: e.type,
        }
      })
      // Write positions into reactive ref — this triggers allNodes computed
      const newPositions = {}
      nodesCopy.forEach(n => {
        newPositions[n.id] = { x: n.x, y: n.y }
      })
      nodePositions.value = newPositions
    })
}

watch(() => props.nodes, () => {
  nextTick(runSimulation)
}, { deep: true })

function updateSize() {
  if (containerRef.value) {
    width.value = containerRef.value.clientWidth || 800
    height.value = containerRef.value.clientHeight || 600
  }
}

onMounted(() => {
  updateSize()
  window.addEventListener('resize', updateSize)
  if (props.nodes.length) runSimulation()
})

onBeforeUnmount(() => {
  simulation?.stop()
  window.removeEventListener('resize', updateSize)
})
</script>

<style scoped>
.graph-canvas {
  flex: 1;
  position: relative;
  background: var(--clr-bg);
  overflow: hidden;
}

svg {
  width: 100%;
  height: 100%;
  display: block;
}

.graph-edge {
  stroke: #444;
  stroke-width: 1;
}
.graph-edge--memory_of { stroke: #666; stroke-width: 1.5; }
.graph-edge--same_tags { stroke: #333; stroke-width: 1; }
.graph-edge--lineage { stroke: #ffeb3b; stroke-width: 1.5; }
.graph-edge--same_session { stroke: #2a2a2a; stroke-width: 1; }

.node-label {
  font-family: 'Share Tech Mono', monospace;
  font-size: 9px;
  fill: rgba(255,255,255,0.7);
  text-transform: uppercase;
  pointer-events: none;
}

.graph-node--selected circle {
  stroke: var(--clr-yellow);
  stroke-width: 3;
}

.graph-tooltip {
  position: absolute;
  background: var(--clr-panel);
  border: 2px solid var(--clr-border);
  padding: 8px 12px;
  pointer-events: none;
  max-width: 240px;
  z-index: 100;
}

.tooltip-label {
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 700;
  color: var(--clr-yellow);
  letter-spacing: 0.1em;
  text-transform: uppercase;
  margin-bottom: 4px;
}

.tooltip-theory {
  font-family: var(--font-mono);
  font-size: 9px;
  color: var(--clr-text-dim);
  line-height: 1.4;
  margin-bottom: 4px;
}

.tooltip-metric {
  font-family: var(--font-mono);
  font-size: 9px;
  color: var(--clr-text-dim);
}

.tooltip-outcome { font-family: var(--font-mono); font-size: 9px; font-weight: 700; }
.tooltip-outcome--positive { color: #4caf50; }
.tooltip-outcome--negative { color: #f44336; }
.tooltip-outcome--neutral { color: #ffeb3b; }

.empty-graph {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  pointer-events: none;
}

.empty-graph span {
  font-family: var(--font-mono);
  font-size: 14px;
  color: var(--clr-text-dim);
  letter-spacing: 0.2em;
  text-transform: uppercase;
}

.empty-sub {
  font-size: 9px !important;
}
</style>
