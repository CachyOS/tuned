from . import base
from .decorators import *
import tuned.logs

log = tuned.logs.get()

DBUS_SERVICE = "org.scx.Loader"
DBUS_PATH = "/org/scx/Loader"
DBUS_IFACE = "org.scx.Loader"
DBUS_PROPS_IFACE = "org.freedesktop.DBus.Properties"

MODE_MAP = {
	"auto": 0,
	"gaming": 1,
	"powersave": 2,
	"lowlatency": 3,
	"server": 4,
}

MODE_REVERSE = {v: k for k, v in MODE_MAP.items()}


def _get_dbus_proxy():
	"""Connect to the scx_loader D-Bus service. Returns (proxy, iface) or (None, None)."""
	try:
		import dbus
		bus = dbus.SystemBus()
		proxy = bus.get_object(DBUS_SERVICE, DBUS_PATH)
		iface = dbus.Interface(proxy, DBUS_IFACE)
		return proxy, iface
	except Exception as e:
		log.info("scx: scx_loader D-Bus service not available: %s" % e)
		return None, None


def _get_property(proxy, prop_name):
	"""Read a D-Bus property from the scx_loader service."""
	try:
		import dbus
		props = dbus.Interface(proxy, DBUS_PROPS_IFACE)
		return props.Get(DBUS_IFACE, prop_name)
	except Exception as e:
		log.warning("scx: failed to read property '%s': %s" % (prop_name, e))
		return None


class ScxPlugin(base.Plugin):
	"""
	Controls sched-ext schedulers via the scx_loader D-Bus service
	(org.scx.Loader).

	CachyOS ships sched-ext schedulers managed by scx_loader.  This
	plug-in allows tuned profiles to select which scheduler runs and in
	what mode.  On systems without scx_loader the plug-in does nothing
	and logs an informational message.

	`scheduler`:::
	Name of the sched-ext scheduler to activate (e.g. `scx_bpfland`,
	`scx_lavd`).  Set to `none` or leave empty to stop any running
	scheduler.

	`mode`:::
	Scheduler mode.  Accepted values: `auto`, `gaming`, `lowlatency`,
	`powersave`, `server`.

	.Gaming profile — low-latency scheduler
	====
	----
	[scx]
	scheduler=scx_lavd
	mode=gaming
	----
	====

	.Desktop profile — balanced scheduler
	====
	----
	[scx]
	scheduler=scx_bpfland
	mode=auto
	----
	====
	"""

	@classmethod
	def _get_config_options(cls):
		return {
			"scheduler": None,
			"mode": None,
		}

	def _instance_init(self, instance):
		instance._has_static_tuning = True
		instance._has_dynamic_tuning = False

	def _instance_cleanup(self, instance):
		pass

	def _validate_scheduler(self, proxy, name):
		"""Check if the scheduler name is in supported_schedulers."""
		supported = _get_property(proxy, "SupportedSchedulers")
		if supported is None:
			return True
		supported = [str(s) for s in supported]
		if name not in supported:
			log.warning("scx: scheduler '%s' not in supported list: %s"
					% (name, ", ".join(supported)))
			return False
		return True

	@command_set("scheduler")
	def _set_scheduler(self, value, instance, sim, remove):
		if value is None:
			return None

		value = str(value).strip()

		proxy, iface = _get_dbus_proxy()
		if proxy is None:
			return None

		# Parse the value.  Two formats are accepted:
		#   - Plain scheduler name from profile config (e.g. "scx_bpfland")
		#     Mode is read from instance.options["mode"].
		#   - Encoded "name:mode_num" saved by _get_scheduler and passed
		#     back by the framework during cleanup/restore.
		#   - "none" sentinel meaning no scheduler was running.
		if value.lower() in ("none", ""):
			if not sim:
				log.info("scx: stopping scheduler")
				try:
					iface.StopScheduler()
				except Exception as e:
					log.warning("scx: failed to stop scheduler: %s" % e)
			return "none"

		if ":" in value:
			# Encoded format from framework restore: "name:mode_num"
			parts = value.split(":", 1)
			sched_name = parts[0]
			try:
				mode_num = int(parts[1])
			except (ValueError, IndexError):
				mode_num = 0
		else:
			# Plain name from profile config
			sched_name = value
			mode_opt = instance.options.get("mode", "auto")
			if mode_opt is None:
				mode_opt = "auto"
			mode_opt = self._variables.expand(str(mode_opt))
			mode_str = mode_opt.strip().lower()
			mode_num = MODE_MAP.get(mode_str)
			if mode_num is None:
				log.warning("scx: invalid mode '%s', expected one of: %s"
						% (mode_str, ", ".join(sorted(MODE_MAP.keys()))))
				return None

		if not self._validate_scheduler(proxy, sched_name):
			return None

		mode_str = MODE_REVERSE.get(mode_num, str(mode_num))
		if not sim:
			log.info("scx: switching to scheduler '%s' in mode '%s'" % (sched_name, mode_str))
			try:
				iface.SwitchScheduler(sched_name, mode_num)
			except Exception as e:
				log.warning("scx: failed to switch scheduler: %s" % e)
				return None
		return "%s:%d" % (sched_name, mode_num)

	@command_get("scheduler")
	def _get_scheduler(self, instance):
		proxy, _ = _get_dbus_proxy()
		if proxy is None:
			return None
		current = _get_property(proxy, "CurrentScheduler")
		if current and str(current):
			mode_val = _get_property(proxy, "SchedulerMode")
			mode_num = int(mode_val) if mode_val is not None else 0
			return "%s:%d" % (str(current), mode_num)
		return "none"

	@command_set("mode")
	def _set_mode(self, value, instance, sim, remove):
		# Mode is applied together with the scheduler in _set_scheduler.
		# This handler validates the value and returns None when
		# scx_loader is absent so that verification is skipped.
		if value is None:
			return None
		proxy, _ = _get_dbus_proxy()
		if proxy is None:
			return None
		value = str(value).strip().lower()
		if value not in MODE_MAP:
			if not sim:
				log.warning("scx: invalid mode '%s', expected one of: %s"
						% (value, ", ".join(sorted(MODE_MAP.keys()))))
			return None
		return value

	@command_get("mode")
	def _get_mode(self, instance):
		proxy, _ = _get_dbus_proxy()
		if proxy is None:
			return None
		mode_val = _get_property(proxy, "SchedulerMode")
		if mode_val is not None:
			return MODE_REVERSE.get(int(mode_val), str(mode_val))
		return None
