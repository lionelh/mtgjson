"""
MTGJSON simple utilities
"""
import collections
import hashlib
import inspect
import itertools
import logging
import os
import pathlib
import time
from typing import Any, Callable, List, Optional, Tuple, Union

import gevent.pool
import requests
import requests.adapters
import requests_cache
import urllib3

from . import consts
from .consts import BAD_FILE_NAMES, CACHE_PATH, LOG_PATH, USE_CACHE

LOGGER = logging.getLogger(__name__)


def init_logger() -> None:
    """
    Initialize the main system logger
    """
    LOG_PATH.mkdir(parents=True, exist_ok=True)

    start_time = time.strftime("%Y-%m-%d_%H.%M.%S")

    logging.basicConfig(
        level=logging.DEBUG
        if os.environ.get("MTGJSON5_DEBUG", "").lower() in ["true", "1"]
        else logging.INFO,
        format="[%(levelname)s] %(asctime)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(str(LOG_PATH.joinpath(f"mtgjson_{start_time}.log"))),
        ],
    )
    logging.getLogger("urllib3").setLevel(logging.ERROR)


def url_keygen(unique_seed: Union[int, str], with_leading: bool = True) -> str:
    """
    Generates a key that MTGJSON will use for redirection
    :param unique_seed: Link seed
    :param with_leading: Should URL be included
    :return: URL Key
    """
    return_value = "https://mtgjson.com/links/" if with_leading else ""
    return f"{return_value}{hashlib.sha256(str(unique_seed).encode()).hexdigest()[:16]}"


def to_camel_case(snake_str: str) -> str:
    """
    Convert "snake_case" => "snakeCase"
    :param snake_str: Snake String
    :return: Camel String
    """
    components = snake_str.split("_")
    return components[0] + "".join(x.title() for x in components[1:])


def parse_magic_rules_subset(
    magic_rules: str, start_header: str = "", end_header: str = ""
) -> str:
    """
    Split up the magic rules to get a smaller working subset for parsing
    :param magic_rules: Magic rules to split up
    :param start_header: Start of content
    :param end_header: End of content
    :return: Smaller set of content
    """
    # Keyword actions are found in section XXX
    if start_header and end_header:
        magic_rules = magic_rules.split(start_header)[2].split(end_header)[0]

    # Windows line endings... yuck
    valid_line_segments = "\n".join(magic_rules.splitlines())

    return valid_line_segments


def retryable_session(
    retries: int = 8,
) -> Union[requests.Session, requests_cache.CachedSession]:
    """
    Session with requests to allow for re-attempts at downloading missing data
    :param retries: How many retries to attempt
    :return: Session that does downloading
    """
    if USE_CACHE:
        stack = inspect.stack()
        calling_class = stack[1][0].f_locals["self"].__class__.__name__
        session = requests_cache.CachedSession(str(CACHE_PATH.joinpath(calling_class)))
    else:
        session = requests.Session()

    retry = urllib3.util.retry.Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=0.3,
        status_forcelist=(500, 502, 504),
    )

    adapter = requests.adapters.HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    session.headers.update({"User-Agent": "Mozilla/5.0 Firefox/75.0 www.mtgjson.com"})
    return session


def parallel_call(
    function: Callable,
    args: Any,
    repeatable_args: Union[Tuple[Any, ...], List[Any]] = None,
    fold_list: bool = False,
    fold_dict: bool = False,
    force_starmap: bool = False,
    pool_size: int = 32,
) -> Any:
    """
    Execute a function in parallel
    :param function: Function to execute
    :param args: Args to pass to the function
    :param repeatable_args: Repeatable args to pass with the original args
    :param fold_list: Compress the results into a 1D list
    :param fold_dict: Compress the results into a single dictionary
    :param force_starmap: Force system to use Starmap over normal selection process
    :param pool_size: How large the gevent pool should be
    :return: Results from execution, with modifications if desired
    """
    pool = gevent.pool.Pool(pool_size)

    if repeatable_args:
        extra_args_rep = [itertools.repeat(arg) for arg in repeatable_args]
        results = pool.map(lambda g_args: function(*g_args), zip(args, *extra_args_rep))
    elif force_starmap:
        results = pool.map(lambda g_args: function(*g_args), args)
    else:
        results = pool.map(function, args)

    if fold_list:
        return list(itertools.chain.from_iterable(results))

    if fold_dict:
        return dict(collections.ChainMap(*results))

    return results


def sort_internal_lists(data: Any) -> Any:
    """
    Sort all lists & sets within a given data structure
    :param data: Data structure to internally sort
    :return Data structure with sorted lists
    """
    if isinstance(data, dict):
        for key, value in data.items():
            data[key] = sort_internal_lists(value)
    elif isinstance(data, (set, list)):
        return sorted(list(data))

    return data


def fix_windows_set_name(set_name: str) -> str:
    """
    In the Windows OS, there are certain file names that are not allowed.
    In case we have a set with such a name, we will add a _ to the end to allow its existence
    on Windows.
    :param set_name: Set name
    :return: Set name with a _ if necessary
    """
    if set_name in BAD_FILE_NAMES:
        return set_name + "_"

    return set_name


def get_file_hash(file_to_hash: pathlib.Path, block_size: int = 65536) -> str:
    """
    Given a file, generate a hash of the contents
    :param file_to_hash: File to generate the hash of
    :param block_size: How big a chunk to read in at a time
    :return file hash
    """
    if not file_to_hash.is_file():
        LOGGER.warning(f"Unable to find {file_to_hash}, no hashes generated")
        return ""

    # Hash can be adjusted in consts.py file
    hash_operation = consts.HASH_TO_GENERATE.copy()

    with file_to_hash.open("rb") as file:
        while True:
            data = file.read(block_size)
            if not data:
                break
            hash_operation.update(data)

    return hash_operation.hexdigest()


def get_str_or_none(value: Any) -> Optional[str]:
    """
    Given a value, get its string representation
    or None object
    :param value: Input value
    :return String value of input or None
    """
    if not value:
        return None

    return str(value)


def send_push_notification(message: str) -> bool:
    """
    Send a push notification to project maintainers.
    These alerts can be disabled by removing the Pushover
    category from the properties file.
    :param message: Message to send
    :return If the message send successfully to everyone
    """
    pushover_app_token = consts.CONFIG.get("Pushover", "app_token")
    pushover_app_users = list(
        filter(None, consts.CONFIG.get("Pushover", "user_tokens").split(","))
    )

    if not pushover_app_token:
        LOGGER.warning("Unable to send Pushover notification. App token not set.")
        return False

    if not pushover_app_users:
        LOGGER.warning("Unable to send Pushover notification. No user keys set.")
        return False

    all_succeeded = True
    for user in pushover_app_users:
        response = requests.post(
            "https://api.pushover.net/1/messages.json",
            data={
                "token": pushover_app_token,
                "user": user,
                "title": f"MTGJSON {consts.MTGJSON_VERSION}",
                "message": message,
            },
        )
        if not response.ok:
            LOGGER.warning(f"Error sending Pushover notification: {response.text}")
            all_succeeded = False

    return all_succeeded
