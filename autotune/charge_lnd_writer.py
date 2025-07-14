import tempfile
import shutil

import logging

logger = logging.getLogger(__name__)

def write_charge_lnd_toml(recommendations, out_path, channels):
    with tempfile.NamedTemporaryFile("w", delete=False) as tmp:
        for section, vals in recommendations.items():
            chconf = channels.get(section)
            if not chconf or "node_id" not in chconf:
                logger.error("Missing node_id for section", extra={'section': section})
                raise KeyError(f"Missing node_id for [{section}] in channels config")
            logger.info("Writing TOML for section", extra={'section': section})
            tmp.write(f"[{section}]\n")
            tmp.write("strategy = static\n")
            tmp.write(f'node.id = {chconf["node_id"]}\n')
            for k, v in vals.items():
                logger.debug(f"{k} = {v}", extra={'section': section})
                tmp.write(f"{k} = {v}\n")
            tmp.write("\n")
        tmp_path = tmp.name
    shutil.move(tmp_path, out_path)
    logger.info(f"charge-lnd config written to {out_path}", extra={'section': 'global'})
 