from unittest.mock import Mock, patch

import pytest
import scrape


def test_download_waits_for_visible_button(tmp_path):
    button = Mock()
    button.is_displayed.return_value = True
    button.location = {"y": 400}
    csv = Mock()
    csv.is_displayed.return_value = True
    csv.location = {"y": 500}
    driver = Mock()
    driver.find_elements.side_effect = [[], [button], [csv]]
    with patch.object(scrape.time, "sleep"), pytest.raises(RuntimeError, match="download timed out"):
        scrape.download_backtest_csv(driver, tmp_path, timeout=1)
    assert driver.find_elements.call_count >= 3
    button.click.assert_called_once()
    csv.click.assert_called_once()
