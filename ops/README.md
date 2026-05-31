# Production ops notes

## WB catalog egress

`wb-repricer-card-egress` routes only Docker traffic from the repricer network to `card.wb.ru:443` through the dedicated eth0 address `10.129.0.32` using fwmark `0x132` and route table `132`.

Install/update on the server:

```bash
sudo install -m 755 ops/wb-repricer-card-egress.sh /usr/local/sbin/wb-repricer-card-egress.sh
sudo install -m 644 ops/systemd/wb-repricer-card-egress.service /etc/systemd/system/wb-repricer-card-egress.service
sudo install -m 644 ops/systemd/wb-repricer-card-egress.timer /etc/systemd/system/wb-repricer-card-egress.timer
sudo systemctl daemon-reload
sudo systemctl enable --now wb-repricer-card-egress.service wb-repricer-card-egress.timer
```

The timer refreshes the resolved `card.wb.ru` IPs every 30 minutes. Keep `SRC_NET` in the script aligned with the Docker network subnet.

## Network routing after reboot

Production keeps the normal server default route on `eth1` / `10.129.0.6` (`51.250.23.69`). `eth0` / `10.129.0.32` maps to the dedicated WB egress address `89.169.175.194` and does not install a normal default route.

The persisted netplan/cloud-init files are archived under `ops/netplan/` and `ops/cloud-init/`. Apply them only on this server/network layout.
