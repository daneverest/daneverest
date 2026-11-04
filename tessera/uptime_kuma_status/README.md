# Uptime Kuma status

A status page for your Tessera screen: the monitors you choose from Uptime Kuma, each with a coloured dot and its
state, under a banner that sums up the lot (All systems operational, Partial outage, Degraded service, Under
maintenance). On a single cell it shrinks to a summary: how many are up, and the worst news.

It reads the entities of Home Assistant's own **Uptime Kuma** integration. Nothing is fetched from Uptime Kuma by the
screen or the Tessera app, so it works with a Kuma that is only reachable inside your home, and needs no key.

## Set up

1. In Home Assistant, add the **Uptime Kuma** integration (Settings, Devices & services, Add integration) and point it
   at your Uptime Kuma. It makes a device per monitor, each with a **Status** sensor such as `sensor.internet_status`.
2. Add this plugin to a screen in Tessera. The screen builds once.
3. In Layout, place the **Status page** tile from the library's Plugins group. Give it two columns or more for the full
   page; a single cell shows the summary.
4. In the inspector, choose up to ten monitors: pick each monitor's **Status** sensor. Give the page a title if you like.
5. Leave **Problems first** on to keep monitors that are down, pending or in maintenance at the top, so they stay in
   view when the tile is too small for every monitor.

## How it reads the states

| Uptime Kuma | Dot | Banner |
|---|---|---|
| Up | Green | All systems operational, when every monitor is up |
| Pending | Amber | Degraded service, when nothing is down |
| Maintenance | Blue | Under maintenance, when nothing is down or pending |
| Down | Red | Partial outage, or Major outage when every monitor with data is down |
| Unavailable or unknown | Grey | No data from Uptime Kuma, when no monitor has data |

## Good to know

- Choose the **Status** sensor of each monitor. Another sensor (its response time, its uptime) shows as No data.
- The integration names the sensor "Internet Status"; the tile shows "Internet".
- A monitor group in Uptime Kuma has a Status sensor of its own: choose it to show a whole group as one row.
- The screen updates the moment Home Assistant does; Home Assistant polls Uptime Kuma about every 30 seconds.
- Uptime Kuma's own status pages are not something the integration exposes; this tile is a status page you put together
  from its monitors.

## Privacy

The plugin uses no service outside your home. The Tessera app sends the screen the state and name of the entities you
chose, as it does for any tile.
