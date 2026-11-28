<template>
  <div class="app-shell">
    <aside class="app-side">
      <h1 class="app-title">航次编排协作台</h1>
      <nav class="nav-list">
        <RouterLink v-for="item in navItems" :key="item.path" :to="item.path" class="nav-item">
          {{ item.label }}
        </RouterLink>
      </nav>
    </aside>
    <main class="app-main">
      <header class="app-head">
        <span class="head-desc">航运调度员、船长和港口代理共同编排航线、分配船舶、登记货物并跟踪每个航次的装载与到港状态。</span>
        <span class="head-user">当前值班：应用开发组</span>
      </header>
      <RouterView />
    </main>
  </div>
</template>

<script setup lang="ts">
const navItems = [{ label: "运营概览", path: "/" }, { label: "船舶档案", path: "/vessel" }, { label: "航线编排", path: "/route" }, { label: "航次编排", path: "/voyage" }, { label: "货物登记", path: "/cargo" }, { label: "船员排班", path: "/crew_shift" }, { label: "燃料记录", path: "/fuel_log" }, { label: "港口挂靠", path: "/port_call" }, { label: "箱号登记", path: "/container" }, { label: "气象预警", path: "/weather_alert" }, { label: "预警补正回执", path: "/weather_receipt" }, { label: "预警处置", path: "/alert_disposition" }, { label: "事故记录", path: "/accident_log" }, { label: "船舶检验", path: "/inspection" }, { label: "通关申报", path: "/clearance" }, { label: "租船合约", path: "/charter" }, { label: "泊位安排", path: "/berth" }, { label: "舱单编制", path: "/manifest" }, { label: "航海日志", path: "/voyage_log" }, { label: "船舶保养", path: "/maintenance" }, { label: "船员证书", path: "/crew_cert" }, { label: "调度指令", path: "/dispatch_note" }, { label: "费率核算", path: "/tariff" }]
</script>
