#![allow(dead_code, non_camel_case_types, unused_unsafe, unused_variables)]
#![allow(non_upper_case_globals, non_snake_case, unused_imports)]

use wayland_client;
use wayland_client::protocol::*;

pub mod __interfaces {
    pub use wayland_client::protocol::__interfaces::*;
    wayland_scanner::generate_interfaces!("protocols/kde-output-v2.xml");
}
use self::__interfaces::*;

wayland_scanner::generate_client_code!("protocols/kde-output-v2.xml");
