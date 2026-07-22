<?php
/**
 * Plugin Name: Synctify Sync Helper
 * Description: Notion → n8n → WordPress 自動上稿流程的輔助端點：開啟 Arconix FAQ REST、寫入 Elementor data、讀寫 TranslatePress 字典表、寫入 AIOSEO meta。
 * Version: 0.1.0
 * Author: Synctify Marketing (Fay)
 *
 * 安裝：外掛 → 上傳外掛（打包成 zip），或直接放入 wp-content/mu-plugins/
 * 認證：所有自訂端點皆要求 Application Password（Basic Auth）且使用者具 edit_posts 權限
 */

if ( ! defined( 'ABSPATH' ) ) exit;

/* ---------------------------------------------------------------
 * 1. Arconix FAQ post type 開啟 REST
 * ------------------------------------------------------------- */
add_filter( 'register_post_type_args', function ( $args, $post_type ) {
	if ( 'faq' === $post_type ) {
		$args['show_in_rest'] = true;
		$args['rest_base']    = 'faq';
	}
	return $args;
}, 10, 2 );

add_filter( 'register_taxonomy_args', function ( $args, $taxonomy ) {
	// Arconix FAQ 的分組 taxonomy（實際名稱若不同，改這裡）
	if ( 'group' === $taxonomy ) {
		$args['show_in_rest'] = true;
		$args['rest_base']    = 'faq-group';
	}
	return $args;
}, 10, 2 );

/* ---------------------------------------------------------------
 * 2. 自訂 REST 端點
 * ------------------------------------------------------------- */
add_action( 'rest_api_init', function () {

	$permission = function () {
		return current_user_can( 'edit_posts' );
	};

	/* 2a. 寫入 Elementor data（protected meta，標準 REST 不開放）
	 * POST /wp-json/synctify/v1/elementor/<post_id>
	 * body: { "elementor_data": [ ...Elementor JSON 陣列... ] }
	 */
	register_rest_route( 'synctify/v1', '/elementor/(?P<id>\d+)', array(
		'methods'             => 'POST',
		'permission_callback' => $permission,
		'callback'            => function ( WP_REST_Request $req ) {
			$post_id = (int) $req['id'];
			if ( ! get_post( $post_id ) ) {
				return new WP_Error( 'not_found', 'Post not found', array( 'status' => 404 ) );
			}
			$data = $req->get_json_params();
			if ( empty( $data['elementor_data'] ) || ! is_array( $data['elementor_data'] ) ) {
				return new WP_Error( 'bad_request', 'elementor_data (array) is required', array( 'status' => 400 ) );
			}

			// 覆蓋前備份（保留最近 3 份）
			$backups   = get_post_meta( $post_id, '_synctify_elementor_backups', true ) ?: array();
			$current   = get_post_meta( $post_id, '_elementor_data', true );
			if ( $current ) {
				array_unshift( $backups, array( 'time' => current_time( 'mysql' ), 'data' => $current ) );
				$backups = array_slice( $backups, 0, 3 );
				update_post_meta( $post_id, '_synctify_elementor_backups', $backups );
			}

			// _elementor_data 以 JSON 字串儲存；wp_slash 防止反斜線被剝除
			update_post_meta( $post_id, '_elementor_data', wp_slash( wp_json_encode( $data['elementor_data'] ) ) );
			update_post_meta( $post_id, '_elementor_edit_mode', 'builder' );
			update_post_meta( $post_id, '_elementor_template_type', 'wp-post' );
			if ( defined( 'ELEMENTOR_VERSION' ) ) {
				update_post_meta( $post_id, '_elementor_version', ELEMENTOR_VERSION );
			}

			// 清除該文章的 Elementor CSS 快取，強制重新生成
			if ( class_exists( '\Elementor\Plugin' ) ) {
				\Elementor\Plugin::$instance->files_manager->clear_cache();
			}

			return array( 'ok' => true, 'post_id' => $post_id, 'backups_kept' => count( $backups ) );
		},
	) );

	/* 2b. 還原 Elementor data 備份
	 * POST /wp-json/synctify/v1/elementor/<post_id>/restore  body: { "index": 0 }
	 */
	register_rest_route( 'synctify/v1', '/elementor/(?P<id>\d+)/restore', array(
		'methods'             => 'POST',
		'permission_callback' => $permission,
		'callback'            => function ( WP_REST_Request $req ) {
			$post_id = (int) $req['id'];
			$index   = (int) ( $req->get_json_params()['index'] ?? 0 );
			$backups = get_post_meta( $post_id, '_synctify_elementor_backups', true );
			if ( empty( $backups[ $index ] ) ) {
				return new WP_Error( 'not_found', 'Backup not found', array( 'status' => 404 ) );
			}
			update_post_meta( $post_id, '_elementor_data', wp_slash( $backups[ $index ]['data'] ) );
			if ( class_exists( '\Elementor\Plugin' ) ) {
				\Elementor\Plugin::$instance->files_manager->clear_cache();
			}
			return array( 'ok' => true, 'restored_from' => $backups[ $index ]['time'] );
		},
	) );

	/* 2c. TranslatePress 字典表查詢
	 * POST /wp-json/synctify/v1/tp/lookup
	 * body: { "language": "zh_CN", "strings": [ "原文1", "原文2", ... ] }
	 * 回傳每筆的 translated 與 status（0=未翻譯 1=機翻 2=人工）；不在表中的回傳 status=-1
	 */
	register_rest_route( 'synctify/v1', '/tp/lookup', array(
		'methods'             => 'POST',
		'permission_callback' => $permission,
		'callback'            => function ( WP_REST_Request $req ) {
			$table = synctify_tp_table( $req->get_json_params()['language'] ?? '' );
			if ( is_wp_error( $table ) ) return $table;
			$strings = $req->get_json_params()['strings'] ?? array();
			if ( ! is_array( $strings ) || empty( $strings ) ) {
				return new WP_Error( 'bad_request', 'strings (array) is required', array( 'status' => 400 ) );
			}
			global $wpdb;
			$out = array();
			foreach ( $strings as $s ) {
				$row = $wpdb->get_row( $wpdb->prepare(
					"SELECT id, translated, status FROM {$table} WHERE original = %s LIMIT 1", $s
				) );
				$out[] = array(
					'original'   => $s,
					'id'         => $row ? (int) $row->id : null,
					'translated' => $row ? $row->translated : null,
					'status'     => $row ? (int) $row->status : -1,
				);
			}
			return $out;
		},
	) );

	/* 2d. TranslatePress 字典表寫入譯文
	 * POST /wp-json/synctify/v1/tp/update
	 * body: { "language": "zh_CN", "items": [ { "id": 123, "translated": "譯文" }, ... ] }
	 * 一律寫入 status=1（機器翻譯）；已是 status=2（人工翻譯）的字串跳過不覆蓋
	 */
	register_rest_route( 'synctify/v1', '/tp/update', array(
		'methods'             => 'POST',
		'permission_callback' => $permission,
		'callback'            => function ( WP_REST_Request $req ) {
			$table = synctify_tp_table( $req->get_json_params()['language'] ?? '' );
			if ( is_wp_error( $table ) ) return $table;
			$items = $req->get_json_params()['items'] ?? array();
			global $wpdb;
			$updated = 0; $skipped = 0;
			foreach ( $items as $item ) {
				if ( empty( $item['id'] ) || ! isset( $item['translated'] ) ) continue;
				$status = (int) $wpdb->get_var( $wpdb->prepare(
					"SELECT status FROM {$table} WHERE id = %d", (int) $item['id']
				) );
				if ( 2 === $status ) { $skipped++; continue; } // 人工翻譯不覆蓋
				$wpdb->update(
					$table,
					array( 'translated' => $item['translated'], 'status' => 1 ),
					array( 'id' => (int) $item['id'] ),
					array( '%s', '%d' ), array( '%d' )
				);
				$updated++;
			}
			return array( 'ok' => true, 'updated' => $updated, 'skipped_human' => $skipped );
		},
	) );

	/* 2e. 寫入 AIOSEO meta title / description
	 * POST /wp-json/synctify/v1/seo/<post_id>
	 * body: { "title": "...", "description": "..." }
	 */
	register_rest_route( 'synctify/v1', '/seo/(?P<id>\d+)', array(
		'methods'             => 'POST',
		'permission_callback' => $permission,
		'callback'            => function ( WP_REST_Request $req ) {
			$post_id = (int) $req['id'];
			if ( ! get_post( $post_id ) ) {
				return new WP_Error( 'not_found', 'Post not found', array( 'status' => 404 ) );
			}
			$p = $req->get_json_params();
			if ( ! function_exists( 'aioseo' ) ) {
				return new WP_Error( 'no_aioseo', 'AIOSEO not active', array( 'status' => 501 ) );
			}
			$aioseo_post = \AIOSEO\Plugin\Common\Models\Post::getPost( $post_id );
			$aioseo_post->post_id = $post_id;
			if ( isset( $p['title'] ) )       $aioseo_post->title       = sanitize_text_field( $p['title'] );
			if ( isset( $p['description'] ) ) $aioseo_post->description = sanitize_text_field( $p['description'] );
			$aioseo_post->save();
			return array( 'ok' => true, 'post_id' => $post_id );
		},
	) );
} );

/* ---------------------------------------------------------------
 * TranslatePress 字典表名稱解析（依 TRP 設定驗證目標語言合法性）
 * ------------------------------------------------------------- */
function synctify_tp_table( $language ) {
	global $wpdb;
	$settings = get_option( 'trp_settings' );
	if ( empty( $settings ) ) {
		return new WP_Error( 'no_trp', 'TranslatePress not configured', array( 'status' => 501 ) );
	}
	$default = $settings['default-language'];           // 例：en_US
	$targets = $settings['translation-languages'];      // 例：[en_US, zh_CN]
	if ( ! in_array( $language, $targets, true ) || $language === $default ) {
		return new WP_Error( 'bad_language', 'Invalid target language: ' . $language, array( 'status' => 400 ) );
	}
	// TRP 表名慣例：{prefix}trp_dictionary_{default}_{target}（小寫）
	return $wpdb->prefix . 'trp_dictionary_' . strtolower( $default ) . '_' . strtolower( $language );
}
