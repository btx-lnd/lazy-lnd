import tempfile
import shutil
import logging

logger = logging.getLogger(__name__)


def write_charge_lnd_toml(recommendations, out_path, channels):
    with tempfile.NamedTemporaryFile("w", delete=False) as tmp:
        for section, vals in recommendations.items():
            chconf = channels.get(section)
            logger.debug(chconf)
            if not chconf or "node_id" not in chconf:
                raise KeyError(f"Missing node_id for [{section}] in channels config")
            tmp.write(f"[{section}]\n")
            tmp.write("strategy = proportional\n")
            tmp.write(f'node.id = {chconf["node_id"]}\n')
            logger.debug(f"[{section}]")
            logger.debug("strategy = proportional")
            logger.debug(f'node.id = {chconf["node_id"]}')
            for k, v in vals.items():
                tmp.write(f"{k} = {v}\n")
                logger.debug(f"{k} = {v}")
            tmp.write("\n")
        tmp_path = tmp.name
    shutil.move(tmp_path, out_path)
